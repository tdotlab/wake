"""Hermes journal: ported from the original daily_journal tests, plus Evidence and vault publishing.
All data here is synthetic."""
import _setup  # noqa: F401
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from wake import core
from wake.core import DraftRejected, Journal, SourceChanged, day_bounds
from wake.sources.hermes import HermesSource

TZ = 'America/New_York'
SCHEMA = '''CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT);
CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_name TEXT,
  tool_call_id TEXT, tool_calls TEXT, timestamp REAL, message_uid TEXT, _compressed_summary INTEGER DEFAULT 0);'''


def make_db(path, sessions=(('s1', 'telegram'),)):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.executemany('INSERT INTO sessions VALUES(?,?)', sessions)
    c.commit()
    return c


def add(c, mid, session, role, content, ts, uid=None, tool_name=None, call_id=None, calls=None, compressed=0):
    c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
              (mid, session, role, content, tool_name, call_id, calls, ts, uid or f'u{mid}', compressed))
    c.commit()


def draft_for(day, refs, summary='A short day.'):
    cite = ' '.join(f'({r})' for r in refs)
    return (f'# {day}\n\n## Summary\n{summary}\n\n## Decisions and corrections\n- A decision. {cite}\n\n'
            '## Work and outcomes\nNone.\n\n## Open threads\nNone.\n\n## Coverage and uncertainty\nFixture.\n')


class HermesJournalTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.db = self.root / 'state.db'
        self.journal_dir = self.root / 'journal'
        self.vault = self.root / 'vault' / 'journal' / 'hermes'
        self.start, self.end = day_bounds('2026-10-22', TZ)

    def tearDown(self):
        self.td.cleanup()

    def journal(self, vault=True):
        return Journal(HermesSource(self.db, TZ), self.journal_dir, vault_dir=self.vault if vault else None)

    def write_day(self, refs=('s:s1/m:1',), today='2026-10-23'):
        j = self.journal()
        task = j.prepare(today=today)
        Path(task['draft_path']).write_text(draft_for(task['day'], refs), encoding='utf-8')
        return j.commit(task['day'], today=today)

    def test_extracts_by_message_time_without_writing_the_source(self):
        c = make_db(self.db, [('s1', 'telegram'), ('job', 'cron')])
        s, e = self.start, self.end
        for row in [(1, 's1', 'user', 'Please test the change', s),
                    (2, 's1', 'assistant', 'Reported successful; api_key=TEST_SECRET_SENTINEL', e - 1, 'u2'),
                    (3, 's1', 'user', 'Next day', e),
                    (4, 'job', 'assistant', 'Journal must not summarize itself', s + 1),
                    (5, 's1', 'user', '[CONTEXT COMPACTION — REFERENCE ONLY] old material', s + 2),
                    (6, 's1', 'assistant', 'Reported successful; api_key=TEST_SECRET_SENTINEL', e - 1, 'u2'),
                    (7, 's1', 'tool', '{"exit_code":0}', s + 3)]:
            add(c, *row[:5], uid=row[5] if len(row) > 5 else None)
        c.close()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        source = HermesSource(self.db, TZ)
        result = source.collect('2026-10-22')
        self.assertEqual([x['message_id'] for x in result['messages']], [1, 7, 2])
        self.assertEqual(result['messages'][0]['ref'], 's:s1/m:1')
        self.assertEqual(result['messages'][0]['time'], '00:00')
        self.assertEqual([x['speaker'] for x in result['messages']], ['owner', 'tool', 'agent'])
        self.assertNotIn('TEST_SECRET_SENTINEL', json.dumps(result))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(source.collect('2026-10-22')['fingerprint'], result['fingerprint'])

    def test_tool_results_carry_the_call_that_produced_them(self):
        c = make_db(self.db)
        calls = json.dumps([{'id': 'c1', 'call_id': 'c1', 'type': 'function',
                             'function': {'name': 'terminal', 'arguments': '{"command": "pytest -q"}'}}])
        add(c, 1, 's1', 'user', 'Run the tests', self.start + 1)
        add(c, 2, 's1', 'assistant', '', self.start + 2, calls=calls)
        add(c, 3, 's1', 'tool', '{"exit_code":0,"output":"5 passed"}', self.start + 3, tool_name='terminal', call_id='c1')
        c.close()
        tool = [x for x in HermesSource(self.db, TZ).collect('2026-10-22')['messages'] if x['role'] == 'tool'][0]
        self.assertEqual(tool['call'], 'terminal: command=pytest -q')

    def test_narrative_is_separate_and_labels_who_is_speaking(self):
        c = make_db(self.db, [('s1', 'telegram'), ('sub', 'subagent')])
        add(c, 1, 's1', 'user', 'A real decision ' + 'x' * 3000, self.start + 5)
        add(c, 2, 'sub', 'user', 'Brief written by Hermes', self.start + 6)
        add(c, 3, 's1', 'tool', '{"exit_code":0}', self.start + 7)
        c.close()
        task = self.journal().prepare(today='2026-10-23')
        self.assertEqual(task['narrative_messages'], 2)
        self.assertTrue(Path(task['tool_evidence_path']).is_file())
        text = Path(task['parts'][0]).read_text(encoding='utf-8')
        self.assertLess(max(len(line) for line in text.splitlines()), 1000)
        self.assertIn('| user (not owner) | subagent |', text)
        self.assertIn('| owner | telegram |', text)
        self.assertNotIn('refs', task)

    def test_the_mobile_app_is_t_but_injected_system_turns_are_not(self):
        c = make_db(self.db, [('app', 'gui'), ('room', 'bot_room')])
        add(c, 1, 'app', 'user', 'Ship it', self.start + 1)
        add(c, 2, 'app', 'user', '[System: The active model for this chat has changed to x.]', self.start + 2)
        add(c, 3, 'room', 'user', '[Discussion: plans] You are @default, one participant', self.start + 3)
        c.close()
        speakers = [m['speaker'] for m in HermesSource(self.db, TZ).collect('2026-10-22')['messages']]
        self.assertEqual(speakers, ['owner', 'other', 'other'])

    def test_commit_publishes_with_frontmatter_evidence_and_a_vault_copy(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision ' + 'y' * 900, self.start + 5)
        result = self.write_day()
        text = Path(result['path']).read_text(encoding='utf-8')
        self.assertTrue(text.startswith('---\ntype: journal\ndate: 2026-10-22\nweekday: Thursday\nagent: hermes\n'))
        for line in ('\nmessages: 1\n', '\nwritten_by: agent\n', '\nevidence_refs: 1\n', '\ncreated: ', '\nupdated: '):
            self.assertIn(line, text)
        self.assertIn('\n# 2026-10-22\n', text)
        evidence = text.split('## Evidence', 1)[1]
        line = [x for x in evidence.splitlines() if x.startswith('- `s:s1/m:1`')][0]
        self.assertIn('| owner | telegram | 00:00 | A fixture decision', line)
        self.assertLessEqual(len(line.split(' | ', 4)[4]), core.EXCERPT_LIMIT)
        self.assertEqual((self.vault / '2026-10-22.md').read_text(encoding='utf-8'), text)
        self.assertFalse((self.journal_dir / '.staging' / '2026-10-22').exists(), 'staging is removed after commit')
        self.assertIsNone(self.journal().prepare(today='2026-10-23'))
        c.close()

    def test_draft_validation_names_each_problem(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        c.close()
        j = self.journal()
        task = j.prepare(today='2026-10-23')
        draft = Path(task['draft_path'])
        good = draft_for('2026-10-22', ['s:s1/m:1'])
        cases = {
            'Missing sections: Summary': good.replace('## Summary\nA short day.\n\n', ''),
            'in this order': good.replace('## Open threads', '## Tmp').replace(
                '## Work and outcomes', '## Open threads').replace('## Tmp', '## Work and outcomes'),
            'not in this day': draft_for('2026-10-22', ['s:invented/m:99']),
            'Summary is': draft_for('2026-10-22', ['s:s1/m:1'], summary='word ' * 300),
            'credential': good + 'password: hunter22\n',
            'first line': 'Intro\n' + good,
            'Remove the Evidence section': good + '\n## Evidence\n- made up\n',
            'wikilinks': good + 'See [[Hermes]].\n',
        }
        for expected, text in cases.items():
            draft.write_text(text, encoding='utf-8')
            with self.assertRaises(DraftRejected) as ctx:
                j.commit('2026-10-22', today='2026-10-23')
            self.assertIn(expected, str(ctx.exception))
            self.assertNotIn('hunter22', str(ctx.exception))
        draft.write_text(good, encoding='utf-8')
        j.commit('2026-10-22', today='2026-10-23')

    def start_on_the_22nd(self):
        self.journal_dir.mkdir()
        (self.journal_dir / '.state.json').write_text(json.dumps({'start_day': '2026-10-22', 'days': {}}))

    def test_empty_days_get_no_note_and_do_not_take_the_agents_slot(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'Something on the 23rd', day_bounds('2026-10-23', TZ)[0] + 60)
        c.close()
        self.start_on_the_22nd()
        j = self.journal()
        task = j.prepare(today='2026-10-24')
        self.assertEqual(task['day'], '2026-10-23', "the empty 22nd must not take the night's one LLM slot")
        for folder in (self.journal_dir, self.vault):
            self.assertFalse((folder / '2026-10-22.md').exists(), 'an empty day writes no file anywhere')
        self.assertFalse((self.journal_dir / '.meta' / '2026-10-22.json').exists())
        self.assertEqual(j.published, [], 'nothing published, so nothing to sync')
        state = json.loads((self.journal_dir / '.state.json').read_text())
        self.assertEqual(state['empty'], ['2026-10-22'])
        self.assertIn('2026-10-22', state['days'])

    def test_a_missing_empty_day_counts_as_done_and_is_never_recreated(self):
        make_db(self.db).close()
        self.start_on_the_22nd()
        for today in ('2026-10-24', '2026-10-25', '2026-10-30'):  # inside the recheck window and after it
            j = self.journal()
            self.assertIsNone(j.prepare(today=today))
            self.assertEqual(j.published, [])
        self.assertEqual(list(self.journal_dir.glob('*.md')), [])
        self.assertEqual(list(self.vault.glob('*.md')) if self.vault.exists() else [], [])
        status = self.journal().status(today='2026-10-25')
        self.assertIn('2026-10-22  no conversations, no note', status)
        self.assertNotIn('pending', status)

    def test_conversation_arriving_late_on_an_empty_day_gets_a_note(self):
        c = make_db(self.db)
        self.start_on_the_22nd()
        j = self.journal()
        self.assertIsNone(j.prepare(today='2026-10-23'))
        add(c, 1, 's1', 'user', 'Arrived after the day was recorded as empty', self.start + 5)
        c.close()
        task = j.prepare(today='2026-10-24')
        self.assertEqual(task['day'], '2026-10-22')
        Path(task['draft_path']).write_text(draft_for('2026-10-22', ['s:s1/m:1']), encoding='utf-8')
        j.commit('2026-10-22', today='2026-10-24')
        self.assertTrue((self.vault / '2026-10-22.md').is_file())
        state = json.loads((self.journal_dir / '.state.json').read_text())
        self.assertNotIn('2026-10-22', state['empty'])

    def test_old_empty_notes_are_retired_and_never_copied_to_the_vault(self):
        make_db(self.db).close()
        fingerprint = HermesSource(self.db, TZ).collect('2026-10-22')['fingerprint']
        self.journal_dir.mkdir()
        (self.journal_dir / '.meta').mkdir()
        old = ('---\ntype: journal\ndate: 2026-10-22\nmessages: 0\nwritten_by: script\n---\n\n# 2026-10-22\n\n'
               '## Summary\nNo default-profile Hermes conversations were recorded this day.\n')
        (self.journal_dir / '2026-10-22.md').write_text(old, encoding='utf-8')
        (self.journal_dir / '.meta' / '2026-10-22.json').write_text('{}')
        (self.journal_dir / '.state.json').write_text(
            json.dumps({'start_day': '2026-10-22', 'days': {'2026-10-22': fingerprint}}))
        j = self.journal()
        self.assertEqual(j.rebuild_evidence(today='2026-10-24'), [], 'the evidence backfill skips empty notes')
        self.assertFalse(self.vault.exists() and list(self.vault.glob('*.md')))
        self.assertIsNone(j.prepare(today='2026-10-24'))
        self.assertFalse((self.journal_dir / '2026-10-22.md').exists())
        self.assertFalse((self.journal_dir / '.meta' / '2026-10-22.json').exists())
        self.assertIn('2026-10-22', json.loads((self.journal_dir / '.state.json').read_text())['empty'])
        self.assertEqual(j.published, [])

    def test_a_forced_redo_of_a_day_with_a_note_but_no_conversations_keeps_the_note(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        path = Path(self.write_day()['path'])
        before = path.read_text(encoding='utf-8')
        c.execute('DELETE FROM messages')
        c.commit()
        c.close()
        self.assertIsNone(self.journal().prepare(day='2026-10-22', today='2026-11-30'))
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_late_messages_reopen_a_recent_day(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        self.write_day()
        add(c, 2, 's1', 'user', 'Late persisted message', self.start + 10)
        self.assertEqual(self.journal().prepare(today='2026-10-24')['day'], '2026-10-22')
        c.close()

    def test_pruned_or_edited_days_keep_their_note(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        add(c, 2, 's1', 'user', 'Another message', self.start + 6)
        self.write_day()
        path = self.journal_dir / '2026-10-22.md'
        before = path.read_text(encoding='utf-8')
        c.execute('UPDATE messages SET content = ? WHERE id = 1', ('edited by transcript repair',))
        c.commit()
        self.assertIsNone(self.journal().prepare(today='2026-10-24'), 'an edit is not new conversation')
        c.execute('DELETE FROM messages')
        c.commit()
        self.assertIsNone(self.journal().prepare(today='2026-10-24'), 'a shrink keeps the note')
        add(c, 3, 's1', 'user', 'Arrived after the window', self.start + 7)
        add(c, 4, 's1', 'user', 'And another', self.start + 8)
        add(c, 5, 's1', 'user', 'And a third', self.start + 9)
        self.assertIsNone(self.journal().prepare(today='2026-10-30'), 'old days are final')
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        c.close()

    def test_an_explicit_day_can_still_be_redone(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        add(c, 2, 's1', 'user', 'Another message', self.start + 6)
        self.write_day()
        c.execute('DELETE FROM messages WHERE id = 2')
        c.commit()
        self.assertEqual(self.journal().prepare(day='2026-10-22', today='2026-11-30')['day'], '2026-10-22')
        c.close()

    def test_republishing_keeps_created_and_a_revision(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        first = Path(self.write_day()['path']).read_text(encoding='utf-8')
        add(c, 2, 's1', 'user', 'Late persisted message', self.start + 10)
        self.write_day(refs=('s:s1/m:2',), today='2026-10-24')
        second = (self.journal_dir / '2026-10-22.md').read_text(encoding='utf-8')
        created = [x for x in first.splitlines() if x.startswith('created: ')][0]
        self.assertIn(created + '\n', second)
        self.assertEqual(len(list((self.journal_dir / '.revisions' / '2026-10-22').iterdir())), 1)
        c.close()

    def test_stale_staging_is_never_mixed_in(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        c.close()
        stale = self.journal_dir / '.staging' / '2026-10-22' / 'narrative-009.md'
        stale.parent.mkdir(parents=True)
        stale.write_text('old')
        task = self.journal().prepare(today='2026-10-23')
        self.assertFalse(stale.exists())
        self.assertEqual(len(task['parts']), 1)

    def test_source_change_after_staging_blocks_commit(self):
        c = make_db(self.db)
        add(c, 1, 's1', 'user', 'A fixture decision', self.start + 5)
        j = self.journal()
        task = j.prepare(today='2026-10-23')
        Path(task['draft_path']).write_text(draft_for('2026-10-22', ['s:s1/m:1']), encoding='utf-8')
        add(c, 2, 's1', 'user', 'Late', self.start + 6)
        with self.assertRaises(SourceChanged):
            j.commit('2026-10-22', today='2026-10-23')
        self.assertFalse((self.journal_dir / '2026-10-22.md').exists())
        c.close()

    def test_rejects_the_current_day(self):
        make_db(self.db).close()
        with self.assertRaises(ValueError):
            self.journal().prepare('2026-10-23', today='2026-10-23')

    def test_evidence_backfill_keeps_the_text_and_adds_excerpts(self):
        c = make_db(self.db, [('s1', 'telegram'), ('sub', 'subagent')])
        add(c, 1, 's1', 'user', 'Keep the journal in the vault', self.start + 5)
        add(c, 2, 'sub', 'user', 'Hermes briefing a subagent', self.start + 6)
        c.close()
        self.journal_dir.mkdir()
        old = ('---\ntype: journal\ndate: 2026-10-22\nwritten_by: agent\nfingerprint: abc\n'
               'generated: 2026-10-23T04:31:00+00:00\n---\n\n' + draft_for('2026-10-22', ['s:s1/m:1', 's:sub/m:2']))
        (self.journal_dir / '2026-10-22.md').write_text(old, encoding='utf-8')
        (self.journal_dir / '.state.json').write_text(json.dumps({'start_day': '2026-10-22', 'days': {'2026-10-22': 'abc'}}))
        (self.journal_dir / '.meta').mkdir()
        meta = self.journal_dir / '.meta' / '2026-10-22.json'
        meta.write_text(json.dumps({'written_by': 'agent', 'message_count': 2}))  # from before Evidence existed
        j = self.journal()
        self.assertEqual(j.rebuild_evidence(today='2026-10-31'), [('2026-10-22', 2)])
        self.assertEqual(json.loads(meta.read_text())['evidence_refs'], 2, 'status reads the count from meta')
        new = (self.journal_dir / '2026-10-22.md').read_text(encoding='utf-8')
        self.assertTrue(new.startswith('---\ntype: journal\ndate: 2026-10-22\nevidence_refs: 2\nwritten_by: agent\n'
                                       'fingerprint: abc\ncreated: 2026-10-23\nupdated: 2026-10-31\n'
                                       'generated: 2026-10-23T04:31:00+00:00\n---\n\n# 2026-10-22\n'), new[:300])
        self.assertIn('- `s:sub/m:2` | other | subagent |', new)
        self.assertEqual(j.rebuild_evidence(today='2026-10-31'), [('2026-10-22', 2)])
        self.assertEqual((self.journal_dir / '2026-10-22.md').read_text(encoding='utf-8'), new, 'idempotent')
        self.assertEqual((self.vault / '2026-10-22.md').read_text(encoding='utf-8'), new)

    def test_cli_gates_the_agent_when_there_is_no_work(self):
        make_db(self.db).close()
        env = dict(os.environ, PYTHONPATH=str(_setup.PACKAGE_ROOT))
        base = [sys.executable, '-m', 'wake', 'journal', 'hermes']
        run = subprocess.run(base + ['prepare', '--db', str(self.db), '--journal', str(self.journal_dir),
                                     '--vault', str(self.root / 'novault')], text=True, capture_output=True, env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip().splitlines()[-1], '{"wakeAgent": false}')
        status = subprocess.run(base + ['status', '--journal', str(self.journal_dir), '--vault', str(self.root / 'novault')],
                                text=True, capture_output=True, env=env)
        self.assertIn('no conversations, no note', status.stdout)
        self.assertEqual(list(self.journal_dir.glob('*.md')), [])

    def test_cli_rejects_a_future_day(self):
        env = dict(os.environ, PYTHONPATH=str(_setup.PACKAGE_ROOT))
        run = subprocess.run([sys.executable, '-m', 'wake', 'journal', 'hermes', 'prepare', '--day', '2999-01-01',
                              '--db', str(self.db), '--journal', str(self.journal_dir)],
                             text=True, capture_output=True, env=env)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('completed local days', run.stderr)

    def test_calendar_day_bounds_follow_dst(self):
        start, end = day_bounds('2026-11-01', TZ)
        self.assertEqual(end - start, 25 * 3600)
        start, end = day_bounds('2026-03-08', TZ)
        self.assertEqual(end - start, 23 * 3600)


class CoreTests(unittest.TestCase):
    def test_redaction_patterns(self):
        samples = ['sk-ant-api03-' + 'a' * 40, 'ghp_' + 'b' * 36, 'AIza' + 'c' * 35, 'https://bob:hunter2pw@example.com/x',
                   'Authorization: Bearer abc.def.ghi', 'client_secret="zzzzzz"',
                   '-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----',
                   'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop']
        for s in samples:
            out = core.redact(s)
            self.assertIn('REDACTED', out, s)
        self.assertNotIn('hunter2pw', core.redact(samples[3]))
        self.assertEqual(core.redact('nothing secret here'), 'nothing secret here')

    def test_excerpt_collapses_pastes_and_defuses_links(self):
        m = {'speaker': 'owner', 'role': 'user',
             'content': 'Look at this [pasted: 5000 chars]\nlots of text\n[/pasted] and [[Hermes]] please'}
        self.assertEqual(core.excerpt(m), 'Look at this [pasted: 5000 chars] and [\\[Hermes]] please')
        medium = {'speaker': 'agent', 'role': 'assistant', 'content': 'z' * 1000}
        self.assertEqual(core.excerpt(medium), 'z' * 1000, 'agent lines are no longer cut at 200')
        long = {'speaker': 'agent', 'role': 'assistant', 'content': 'z' * 20000}
        self.assertEqual(len(core.excerpt(long)), core.EXCERPT_LIMIT)


if __name__ == '__main__':
    unittest.main()
