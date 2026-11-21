"""brain.py: lint, find, log, new, stats and sync against throwaway vaults. Synthetic notes only."""
import _setup  # noqa: F401
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
import io
import os
import subprocess
import sys
import tempfile
import unittest

from wake import brain, scaffold

IDENTITY = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
            'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}


def run_brain(vault, *args):
    """Run brain.main in-process. Returns (exit code, stdout)."""
    out = io.StringIO()
    with redirect_stdout(out):
        code = brain.main(['--vault', str(vault), *args])
    return code, out.getvalue()


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class VaultCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, IDENTITY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / 'vault'
        scaffold.init_vault(self.vault, owner='Sam Example')

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def lint(self, *flags):
        return run_brain(self.vault, 'lint', *flags)


NOTE = '---\ntype: concept\ncreated: 2026-01-02\nupdated: 2026-01-02\n---\n\n# {title}\n\n{body}\n'


class LintTests(VaultCase):
    def test_fresh_starter_vault_is_clean_under_strict(self):
        code, out = self.lint('--strict')
        self.assertEqual(code, 0, out)
        for heading in ('Dead links: none', 'Orphans', 'Not in index.md: none', 'Frontmatter problems: none',
                        'Unresolved merge conflict markers: none'):
            self.assertIn(heading, out)

    def test_dead_link_fails_strict(self):
        self.write('wiki/concepts/Alpha.md', NOTE.format(title='Alpha', body='See [[Nowhere At All]] for more detail on this.'))
        code, out = self.lint('--strict')
        self.assertEqual(code, 1)
        self.assertIn('Nowhere At All', out)

    def test_orphan_and_not_in_index_are_reported_but_not_fatal(self):
        self.write('wiki/concepts/Beta.md', NOTE.format(title='Beta', body='A concept with enough words to not be near-empty at all.'))
        code, out = self.lint('--strict')
        self.assertEqual(code, 0, out)
        self.assertIn('wiki/concepts/Beta.md', out)
        self.assertIn('Orphans (no inbound links outside index/log/hot): 1', out)
        self.assertIn('Not in index.md: 1', out)

    def test_linking_and_indexing_clears_both(self):
        self.write('wiki/concepts/Beta.md', NOTE.format(title='Beta', body='A concept with enough words to not be near-empty at all.'))
        index = self.vault / 'index.md'
        index.write_text(index.read_text(encoding='utf-8') + '\n- [[Beta]]: a test concept\n', encoding='utf-8')
        code, out = self.lint('--strict')
        self.assertEqual(code, 0, out)
        self.assertIn('Not in index.md: none', out)

    def test_missing_and_malformed_frontmatter_fail_strict(self):
        self.write('wiki/concepts/Gamma.md', '# Gamma\n\nNo frontmatter, but plenty of words in the body for the length check.\n')
        self.write('wiki/concepts/Delta.md', NOTE.format(title='Delta', body='Words words words words words words words words.')
                   .replace('created: 2026-01-02', 'created: yesterday'))
        code, out = self.lint('--strict')
        self.assertEqual(code, 1)
        self.assertIn('Gamma.md', out)
        self.assertIn('Delta.md', out)

    def test_conflict_markers_fail_strict(self):
        self.write('wiki/concepts/Eps.md', NOTE.format(title='Eps', body='<<<<<<< Updated upstream\nmine\n=======\ntheirs\n>>>>>>> Stashed changes'))
        code, out = self.lint('--strict')
        self.assertEqual(code, 1)
        self.assertIn('Eps.md', out)

    def test_owner_folders_are_not_schema_checked(self):
        self.write('notes/scribble.md', 'no frontmatter, link to [[Nothing]]\n')
        self.write('daily/2026-01-02.md', 'dear diary\n')
        self.write('inbox/clip.md', 'raw clip\n')
        code, out = self.lint('--strict')
        self.assertEqual(code, 0, out)
        self.assertIn('Waiting in inbox/: 1', out)

    def test_a_realistic_journal_note_passes(self):
        self.write('journal/hermes/2026-01/2026-01-02.md',
                   '---\ntype: journal\ndate: 2026-01-02\nweekday: Friday\nagent: hermes\ntimezone: Europe/Berlin\n'
                   'messages: 12\nnarrative_messages: 9\ntool_records: 0\nsessions: 2\ntruncated_excerpts: 0\n'
                   'evidence_refs: 1\nwritten_by: agent\nsource_version: 3\nfingerprint: abc123\n'
                   'created: 2026-01-03\nupdated: 2026-01-03\ngenerated: 2026-01-03T01:00:00+01:00\n'
                   'tags:\n  - journal\n  - journal/hermes\n---\n\n'
                   '# 2026-01-02\n\n## Summary\nShort.\n\n## Evidence\n- `s:1a2b/m:5` | owner | cli | 14:02 | synthetic excerpt\n')
        code, out = self.lint('--strict')
        self.assertEqual(code, 0, out)

    def test_stale_hot_is_flagged(self):
        hot = self.vault / 'hot.md'
        today = brain.TODAY.isoformat()
        text = hot.read_text(encoding='utf-8').replace(f'created: {today}', 'created: 2020-01-01')
        hot.write_text(text.replace(f'updated: {today}', 'updated: 2020-01-01'), encoding='utf-8')
        code, out = self.lint('--strict')
        self.assertEqual(code, 0, out)  # stale is advice, not an error
        self.assertIn('hot.md: stale', out)

    def test_json_output_parses(self):
        import json
        code, out = self.lint('--json')
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(out), dict)


class NewFindLogStatsTests(VaultCase):
    def test_new_creates_from_template_and_refuses_overwrite(self):
        code, out = run_brain(self.vault, 'new', 'project', 'Demo Project')
        self.assertEqual(code, 0, out)
        note = (self.vault / 'projects' / 'Demo Project.md').read_text(encoding='utf-8')
        self.assertIn('type: project', note)
        self.assertIn('# Demo Project', note)
        self.assertNotIn('{{title}}', note)
        self.assertNotIn('{{date}}', note)
        self.assertEqual(run_brain(self.vault, 'new', 'project', 'Demo Project')[0], 1)

    def test_new_person_uses_owner_from_init(self):
        run_brain(self.vault, 'new', 'person', 'Pat Friend')
        note = (self.vault / 'people' / 'Pat Friend.md').read_text(encoding='utf-8')
        self.assertIn('Sam Example', note)
        self.assertNotIn('{{owner}}', note)

    def test_new_rejects_path_tricks_and_unknown_types(self):
        for title in ('../evil', 'a/b', '.hidden', 'trailing.', ' lead'):
            self.assertEqual(run_brain(self.vault, 'new', 'concept', title)[0], 2, title)
        self.assertEqual(run_brain(self.vault, 'new', 'widget', 'Thing')[0], 2)
        self.assertFalse((self.root / 'evil.md').exists())

    def test_log_inserts_newest_first_and_bumps_updated(self):
        run_brain(self.vault, 'log', 'save', 'First', '--agent', 'tester')
        run_brain(self.vault, 'log', 'update', 'Second', '--agent', 'tester', '--detail', 'a detail')
        text = (self.vault / 'log.md').read_text(encoding='utf-8')
        self.assertLess(text.index('| Second'), text.index('| First'))
        self.assertGreater(text.index('<!-- entries -->'), 0)
        self.assertLess(text.index('<!-- entries -->'), text.index('| Second'))
        self.assertIn('a detail', text)

    def test_find_ranks_title_matches_and_skips_raw(self):
        self.write('wiki/concepts/Spaced Repetition.md', NOTE.format(title='Spaced Repetition', body='Review cards at growing intervals.'))
        self.write('raw/clip.md', 'repetition repetition repetition\n')
        code, out = run_brain(self.vault, 'find', 'spaced', 'repetition')
        self.assertEqual(code, 0)
        self.assertIn('Spaced Repetition.md', out)
        self.assertNotIn('raw/clip.md', out)

    def test_stats_counts_notes_and_raw_files(self):
        self.write('raw/2026-01-02-a.md', 'x')
        _, out = run_brain(self.vault, 'stats')
        self.assertIn('notes,', out)
        self.assertIn('raw/ files: 1', out)

    def test_missing_vault_exits_2(self):
        code, out = run_brain(self.root / 'nowhere', 'stats')
        self.assertEqual(code, 2)
        self.assertIn('wake init', out)


class StandaloneScriptTests(VaultCase):
    def test_copied_script_finds_its_own_vault(self):
        script = self.vault / '_system' / 'scripts' / 'brain.py'
        env = {k: v for k, v in os.environ.items() if k not in ('WAKE_VAULT', 'BRAIN_VAULT', 'PYTHONPATH')}
        run = subprocess.run([sys.executable, str(script), 'lint', '--strict'], cwd=self.root, env=env,
                             capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(str(self.vault), run.stdout)

    def test_script_is_a_byte_copy_of_the_package_module(self):
        script = self.vault / '_system' / 'scripts' / 'brain.py'
        self.assertEqual(script.read_bytes(), Path(brain.__file__).read_bytes())


class SyncTests(VaultCase):
    def setUp(self):
        super().setUp()
        git(self.root, 'init', '--bare', '-b', 'main', 'remote.git')
        git(self.vault, 'remote', 'add', 'origin', str(self.root / 'remote.git'))
        git(self.vault, 'push', '-u', 'origin', 'main')
        self.remote = self.root / 'remote.git'

    def test_commits_and_pushes(self):
        self.write('wiki/concepts/Zeta.md', NOTE.format(title='Zeta', body='Pushed through sync.'))
        code, out = run_brain(self.vault, 'sync', '-m', 'tester: add zeta')
        self.assertEqual(code, 0, out)
        self.assertIn('tester: add zeta', git(self.remote, 'log', '--format=%s', '-1'))

    def test_first_push_sets_upstream(self):
        fresh = self.root / 'fresh'
        scaffold.init_vault(fresh)
        git(self.root, 'init', '--bare', '-b', 'main', 'other.git')
        git(fresh, 'remote', 'add', 'origin', str(self.root / 'other.git'))
        code, out = run_brain(fresh, 'sync', '-m', 'tester: first push')
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.root / 'other.git', 'rev-list', '--count', 'main').strip(), '1')

    def test_conflict_aborts_rebase_keeps_local_commit_and_writes_no_markers(self):
        git(self.root, 'clone', 'remote.git', 'other')
        other = self.root / 'other'
        (other / 'hot.md').write_text('upstream version of hot\n', encoding='utf-8')
        git(other, '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-am', 'upstream hot')
        git(other, 'push')
        (self.vault / 'hot.md').write_text('local version of hot\n', encoding='utf-8')
        code, out = run_brain(self.vault, 'sync', '-m', 'tester: local hot')
        self.assertEqual(code, 1)
        self.assertIn('conflict', out.lower())
        self.assertFalse((self.vault / '.git' / 'rebase-merge').exists())
        self.assertFalse((self.vault / '.git' / 'rebase-apply').exists())
        text = (self.vault / 'hot.md').read_text(encoding='utf-8')
        self.assertEqual(text, 'local version of hot\n')
        self.assertNotIn('<<<<<<<', text)
        self.assertIn('tester: local hot', git(self.vault, 'log', '--format=%s', '-1'))
        self.assertEqual(git(self.vault, 'status', '--porcelain').strip(), '')

    def test_clean_pull_then_push(self):
        git(self.root, 'clone', 'remote.git', 'other')
        other = self.root / 'other'
        (other / 'notes').mkdir(exist_ok=True)
        (other / 'notes' / 'x.md').write_text('from elsewhere\n', encoding='utf-8')
        git(other, 'add', '.')
        git(other, '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-m', 'elsewhere')
        git(other, 'push')
        self.write('notes/y.md', 'from here\n')
        code, out = run_brain(self.vault, 'sync', '-m', 'tester: here')
        self.assertEqual(code, 0, out)
        self.assertTrue((self.vault / 'notes' / 'x.md').exists())
        tree = git(self.remote, 'ls-tree', '-r', '--name-only', 'main')
        self.assertIn('notes/x.md', tree)
        self.assertIn('notes/y.md', tree)


if __name__ == '__main__':
    unittest.main()
