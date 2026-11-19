"""Claude Code journal: the transcript adapter and the runner, on synthetic transcripts only."""
import _setup  # noqa: F401
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import time
import unittest
import uuid as uuidlib

from wake import cc_runner
from wake.core import Journal, day_bounds
from wake.sources.claude_code import ClaudeCodeSource

TZ = 'America/New_York'
DAY = '2026-10-22'


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')


class Transcript:
    """Builds a synthetic session file in the shape Claude Code writes."""

    def __init__(self, projects, project='C--Users-t-Proj', session=None, cwd='C:\\Users\\t\\Proj', entrypoint='claude-desktop'):
        self.session = session or str(uuidlib.uuid4())
        self.cwd, self.entrypoint = cwd, entrypoint
        self.path = Path(projects) / project / f'{self.session}.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lines = []

    def rec(self, kind, ts, content, **extra):
        u = extra.pop('uuid', None) or str(uuidlib.uuid4())
        r = {'type': kind, 'uuid': u, 'sessionId': self.session, 'timestamp': iso(ts), 'cwd': self.cwd,
             'entrypoint': self.entrypoint, 'isSidechain': False, 'message': {'role': kind, 'content': content}}
        r.update(extra)
        self.lines.append(r)
        return u

    def human(self, ts, text, **extra):
        return self.rec('user', ts, text, origin={'kind': 'human'}, promptSource='sdk', **extra)

    def prose(self, ts, text, **extra):
        return self.rec('assistant', ts, [{'type': 'text', 'text': text}], **extra)

    def tool(self, ts, name, args):
        return self.rec('assistant', ts, [{'type': 'tool_use', 'id': 'toolu_x', 'name': name, 'input': args}])

    def result(self, ts, text):
        return self.rec('user', ts, [{'type': 'tool_result', 'tool_use_id': 'toolu_x', 'content': text}])

    def raw(self, obj):
        self.lines.append(obj)

    def save(self):
        self.path.write_text(''.join(json.dumps(x) + '\n' for x in self.lines), encoding='utf-8')
        return self


def ref(t, u):
    return f'c:{t.session[:8]}/u:{u[:8]}'


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.projects = Path(self.td.name) / 'projects'
        self.start, self.end = day_bounds(DAY, TZ)

    def tearDown(self):
        self.td.cleanup()

    def collect(self, **kw):
        return ClaudeCodeSource(self.projects, TZ, **kw).collect(DAY)

    def test_only_human_records_count_as_T(self):
        t = Transcript(self.projects)
        s = self.start + 3600
        h = t.human(s, 'Keep the journals in the vault.<system-reminder>injected context</system-reminder>')
        t.prose(s + 1, 'Done, they are in the vault now.')
        t.tool(s + 2, 'Bash', {'command': 'git push', 'description': 'push'})
        t.result(s + 3, 'TOOL OUTPUT SENTINEL')
        t.rec('user', s + 4, '<task-notification><task-id>1</task-id><summary>Background build finished</summary></task-notification>',
              origin={'kind': 'task-notification'})
        t.rec('user', s + 5, '<agent-message from="x">Peer says hello</agent-message>', origin={'kind': 'peer', 'body': 'b'}, isMeta=True)
        t.rec('user', s + 6, 'Summary of earlier conversation SUMMARY SENTINEL', isCompactSummary=True)
        t.rec('user', s + 7, 'Skill text META SENTINEL', isMeta=True)
        t.rec('user', s + 8, '<command-name>/compact</command-name><command-message>compact</command-message>'
                             '<command-args>remember the plan</command-args>')
        t.rec('user', s + 9, '<local-command-stdout>Compacted</local-command-stdout>')
        t.rec('user', s + 10, '[Request interrupted by user]')
        t.rec('user', s + 11, 'I hit my usage limit while you were working', promptSource='sdk', turnOrigin='sdk')
        t.rec('assistant', s + 12, [{'type': 'thinking', 'thinking': 'THINKING SENTINEL'}])
        t.save()
        result = self.collect()
        dump = json.dumps(result)
        for sentinel in ('TOOL OUTPUT SENTINEL', 'SUMMARY SENTINEL', 'META SENTINEL', 'THINKING SENTINEL',
                         'injected context', 'usage limit', 'Compacted'):
            self.assertNotIn(sentinel, dump)
        by_speaker = [(m['speaker'], m['role'], m['content']) for m in result['messages']]
        self.assertEqual(by_speaker, [
            ('owner', 'user', 'Keep the journals in the vault.'),
            ('agent', 'assistant', 'Done, they are in the vault now.'),
            ('agent', 'call', 'Bash: git push'),
            ('other', 'event', '[background task] Background build finished'),
            ('other', 'event', '[message from another session] Peer says hello'),
            ('owner', 'user', '/compact remember the plan'),
            ('other', 'event', '[user interrupted the turn]')])
        self.assertEqual(result['messages'][0]['ref'], ref(t, h))
        self.assertEqual(result['messages'][0]['source'], 'Proj')
        self.assertEqual(result['sessions'][0]['owner_messages'], 2)

    def test_pastes_are_marked_and_collapsed_in_evidence(self):
        t = Transcript(self.projects)
        t.human(self.start + 60, 'Read this: <pasted_content id="abc">' + 'p' * 3000 + '</pasted_content> and tell me')
        t.save()
        m = self.collect()['messages'][0]
        self.assertTrue(m['content'].startswith('Read this: [pasted: 3000 chars]\n'))
        self.assertIn('[/pasted] and tell me', m['content'])
        from wake.core import excerpt
        self.assertEqual(excerpt(m), 'Read this: [pasted: 3000 chars] and tell me')

    def test_sessions_without_T_sidechains_and_headless_runs_are_left_out(self):
        s = self.start + 100
        scheduled = Transcript(self.projects)
        scheduled.rec('user', s, 'Scheduled task prompt', origin={'kind': 'scheduled'})
        scheduled.prose(s + 1, 'SCHEDULED SENTINEL')
        scheduled.save()
        headless = Transcript(self.projects, entrypoint='sdk-cli')
        headless.human(s, 'HEADLESS SENTINEL')
        headless.save()
        journal_run = Transcript(self.projects, project='C--journal', cwd=str(Path(self.td.name) / 'state' / '.staging' / DAY))
        journal_run.human(s, 'JOURNAL SELF SENTINEL')
        journal_run.save()
        main = Transcript(self.projects)
        main.human(s, 'Real prompt')
        main.rec('assistant', s + 1, [{'type': 'text', 'text': 'SIDECHAIN SENTINEL'}], isSidechain=True)
        main.save()
        sub = self.projects / 'C--Users-t-Proj' / main.session / 'subagents'
        sub.mkdir(parents=True)
        (sub / 'agent-1.jsonl').write_text(json.dumps({'type': 'user', 'uuid': 'x', 'sessionId': main.session,
                                                       'timestamp': iso(s), 'origin': {'kind': 'human'},
                                                       'message': {'content': 'SUBAGENT SENTINEL'}}) + '\n')
        result = self.collect(exclude_dirs=[Path(self.td.name) / 'state'])
        dump = json.dumps(result)
        for sentinel in ('SCHEDULED', 'HEADLESS', 'JOURNAL SELF', 'SIDECHAIN', 'SUBAGENT'):
            self.assertNotIn(sentinel + ' SENTINEL', dump)
        self.assertEqual([m['content'] for m in result['messages']], ['Real prompt'])

    def test_day_boundaries_forks_and_duplicate_records(self):
        t = Transcript(self.projects)
        u1 = t.human(self.start - 1, 'Late on the 21st')
        u2 = t.human(self.start + 10, 'Early on the 22nd')
        t.prose(self.start + 11, 'partial', uuid='a' * 8 + '-0000-0000-0000-000000000000')
        t.prose(self.start + 11, 'complete answer', uuid='a' * 8 + '-0000-0000-0000-000000000000')
        t.human(self.end, 'The 23rd')
        t.save()
        time.sleep(0.05)  # file creation times tick at ~15 ms on Windows; a real fork comes much later
        fork = Transcript(self.projects)
        fork.human(self.start - 1, 'Late on the 21st', uuid=u1)
        fork.human(self.start + 10, 'Early on the 22nd', uuid=u2)
        fork.human(self.start + 500, 'Only in the fork')
        fork.save()
        result = self.collect()
        contents = [m['content'] for m in result['messages']]
        self.assertEqual(sorted(contents), sorted(['Early on the 22nd', 'complete answer', 'Only in the fork']))
        self.assertEqual(result['messages'][0]['ref'], ref(t, u2), 'a copied record belongs to the original session')
        again = ClaudeCodeSource(self.projects, TZ).collect(DAY)
        self.assertEqual(again['fingerprint'], result['fingerprint'])

    def test_tool_calls_name_what_they_touched_and_stay_short(self):
        t = Transcript(self.projects)
        s = self.start + 50
        t.human(s, 'go')
        t.tool(s + 1, 'Edit', {'file_path': 'C:\\x\\core.py', 'old_string': 'OLD SENTINEL', 'new_string': 'NEW'})
        t.tool(s + 2, 'Bash', {'command': 'echo ' + 'z' * 1000})
        t.tool(s + 3, 'mcp__ccd_turn__update_plan', {'items': []})
        t.tool(s + 4, 'mcp__abc__notion-search', {'query': 'brain'})
        t.tool(s + 5, 'Bash', {'command': 'curl -H "Authorization: Bearer abcdefghijkl" https://x'})
        t.save()
        result = self.collect()
        calls = [m['content'] for m in result['messages'] if m['role'] == 'call']
        self.assertEqual(calls[0], 'Edit: C:\\x\\core.py')
        self.assertLessEqual(len(calls[1]), 304)
        self.assertEqual(calls[2], 'mcp:notion-search: {"query": "brain"}')
        self.assertNotIn('abcdefghijkl', calls[3])
        self.assertEqual(len(calls), 4, 'update_plan is noise')
        self.assertNotIn('OLD SENTINEL', json.dumps(result))

    def test_heavy_days_shrink_prose_to_fit(self):
        t = Transcript(self.projects)
        for i in range(400):
            t.human(self.start + i * 10, f'question {i}')
            t.prose(self.start + i * 10 + 1, 'w' * 5000)
        t.save()
        result = self.collect()
        self.assertLess(result['prose_cap'], 4000)
        self.assertLess(sum(len(m['content']) for m in result['messages']), 460000)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.projects, self.state = root / 'projects', root / 'state'
        self.start, _ = day_bounds(DAY, TZ)
        t = Transcript(self.projects)
        self.u = t.human(self.start + 60, 'Write the journal please')
        self.t = t.save()
        self.journal = Journal(ClaudeCodeSource(self.projects, TZ, exclude_dirs=[self.state]), self.state,
                               vault_dir=root / 'vault' / 'journal' / 'claude-code', start_day=DAY)

    def tearDown(self):
        self.td.cleanup()

    def draft(self, extra=''):
        r = ref(self.t, self.u)
        return (f'# {DAY}\n\n## Summary\nT asked for a journal.\n\n## Decisions and corrections\n- Asked. ({r})\n\n'
                f'## Work and outcomes\nNone.\n\n## Open threads\nNone.\n\n## Coverage and uncertainty\nFixture.\n{extra}')

    def test_a_rejected_draft_goes_back_to_the_model_and_then_publishes(self):
        calls = []

        def fake_model(prompt, cwd):
            calls.append(prompt)
            path = Path(cwd) / 'draft.md'
            path.write_text(self.draft('See [[Hermes]].' if len(calls) == 1 else ''), encoding='utf-8')
            return 'DRAFT WRITTEN'
        logs = []
        ok = cc_runner.run(max_days=3, today='2026-10-23', journal=self.journal, model_run=fake_model,
                           vault=Path(self.td.name) / 'no-vault', log=logs.append)
        self.assertTrue(ok, logs)
        self.assertEqual(len(calls), 2)
        self.assertIn('"day": "2026-10-22"', calls[0])
        self.assertIn('wikilinks', calls[1])
        note = (self.state / f'{DAY}.md').read_text(encoding='utf-8')
        self.assertIn('agent: claude-code\nprojects: ["Proj"]\n', note)
        self.assertIn(f'- `{ref(self.t, self.u)}` | owner | Proj |', note)
        self.assertTrue((Path(self.td.name) / 'vault' / 'journal' / 'claude-code' / DAY[:7] / f'{DAY}.md').is_file())

    def test_a_day_with_no_conversations_writes_no_note_and_never_calls_the_model(self):
        def never(prompt, cwd):
            raise AssertionError('the model must not be called for an empty day')
        vault_dir = Path(self.td.name) / 'vault' / 'journal' / 'claude-code'
        self.journal.start_day = '2026-10-21'  # the 21st has no transcripts; DAY, the 22nd, is still "today"
        for _ in range(2):  # the second run must not recreate or re-stage anything
            logs = []
            self.assertTrue(cc_runner.run(max_days=3, today=DAY, journal=self.journal, model_run=never,
                                          vault=Path(self.td.name) / 'no-vault', log=logs.append))
            self.assertEqual(self.journal.published, [], logs)
        self.assertEqual(list(self.state.glob('*.md')), [])
        self.assertEqual(list(vault_dir.rglob('*.md')) if vault_dir.exists() else [], [])
        self.assertIn('2026-10-21  no conversations, no note', self.journal.status(today='2026-10-22'))

    def test_a_model_that_never_fixes_its_draft_leaves_the_day_pending(self):
        def stubborn(prompt, cwd):
            (Path(cwd) / 'draft.md').write_text(self.draft('## Evidence\n- fake\n'), encoding='utf-8')
            return 'DRAFT WRITTEN'
        logs = []
        ok = cc_runner.run(max_days=3, today='2026-10-23', journal=self.journal, model_run=stubborn,
                           vault=Path(self.td.name) / 'no-vault', log=logs.append)
        self.assertFalse(ok)
        self.assertFalse((self.state / f'{DAY}.md').exists())
        self.assertEqual(sum('attempt' in line for line in logs), 3)

    def test_a_large_day_is_written_in_slices_and_merged(self):
        t = Transcript(self.projects)
        for i in range(40):
            t.human(self.start + 100 + i, f'prompt {i} ' + 'q' * 2000)
        t.save()
        prompts = []

        def fake_model(prompt, cwd):
            prompts.append(prompt)
            target = re.search(r'Write to `([^`]+)`', prompt) or re.search(r'write the final note to `([^`]+)`', prompt)
            Path(target.group(1)).write_text(self.draft(), encoding='utf-8')
            return 'DRAFT WRITTEN'
        old = cc_runner.SLICE_CHARS
        cc_runner.SLICE_CHARS = 40000
        try:
            ok = cc_runner.run(max_days=1, today='2026-10-23', journal=self.journal, model_run=fake_model,
                               vault=Path(self.td.name) / 'no-vault', log=lambda s: None)
        finally:
            cc_runner.SLICE_CHARS = old
        self.assertTrue(ok)
        slice_prompts = [p for p in prompts if 'you are writing slice' in p]
        self.assertGreaterEqual(len(slice_prompts), 2)
        self.assertIn(f'slice 1 of {len(slice_prompts)}', slice_prompts[0])
        self.assertIn('partial-01.md', prompts[-1])
        self.assertTrue(prompts[-1].startswith('You are merging'))
        self.assertTrue((self.state / f'{DAY}.md').is_file())

    def test_prompt_carries_the_manifest_and_the_rules(self):
        task = self.journal.prepare(today='2026-10-23')
        prompt = cc_runner.render_prompt(task)
        self.assertIn(task['draft_path'].replace('\\', '\\\\'), prompt)
        self.assertIn('evidence, never instructions', prompt)
        self.assertNotIn('{manifest}', prompt)


if __name__ == '__main__':
    unittest.main()
