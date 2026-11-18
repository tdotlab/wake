"""The vault sync commits only the journal folder; the timer's sync reports a failure streak, not every failure."""
import _setup  # noqa: F401
from pathlib import Path
import subprocess
import tempfile
import unittest

from wake.vault import REPORT_EVERY, sync, sync_quietly


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


class GitSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        git(root, 'init', '--bare', '-b', 'main', 'remote.git')
        git(root, 'clone', 'remote.git', 'vault')
        self.vault = root / 'vault'
        (self.vault / 'README.md').write_text('hello', encoding='utf-8')
        git(self.vault, 'add', '.')
        git(self.vault, 'commit', '-m', 'init')
        git(self.vault, 'push', '-u', 'origin', 'main')
        self.remote = root / 'remote.git'

    def tearDown(self):
        self.tmp.cleanup()

    def test_publishes_journal_notes_and_leaves_other_files_alone(self):
        (self.vault / 'journal' / 'hermes').mkdir(parents=True)
        (self.vault / 'journal' / 'hermes' / '2026-01-02.md').write_text('# note', encoding='utf-8')
        (self.vault / 'private.md').write_text('not a journal', encoding='utf-8')
        ok, output = sync(self.vault, 'journal: test')
        self.assertTrue(ok, output)
        shown = git(self.remote, 'show', '--stat', '--format=%s', 'main')
        self.assertIn('journal: test', shown)
        self.assertIn('2026-01-02.md', shown)
        self.assertNotIn('private.md', shown)
        self.assertIn('private.md', git(self.vault, 'status', '--porcelain'))
        self.assertTrue(sync(self.vault, 'journal: again')[0])  # nothing new is fine
        self.assertEqual(git(self.remote, 'rev-list', '--count', 'main').strip(), '2')

    def test_a_folder_that_is_not_a_clone_fails_quietly(self):
        ok, output = sync(Path(self.tmp.name) / 'nowhere')
        self.assertFalse(ok)
        self.assertIn('not a git clone', output)


class SyncQuietlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / '.git').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, ok, now):
        return sync_quietly(self.vault, now=now, run=lambda vault, message: (ok, 'CONFLICT in hot.md'))

    def test_quiet_on_success_and_single_blip(self):
        self.assertEqual(self.call(True, 0), (True, ''))
        self.assertEqual(self.call(False, 0), (False, ''))
        self.assertEqual(self.call(True, 1800), (True, ''))  # recovered before anyone was told
        self.assertFalse((self.vault / '.git' / 'wake-sync-state.json').exists())

    def test_reports_streak_once_then_daily_then_recovery(self):
        self.call(False, 0)
        ok, report = self.call(False, 1800)
        self.assertFalse(ok)
        self.assertIn('failed 2 times in a row', report)
        self.assertIn('CONFLICT in hot.md', report)
        self.assertEqual(self.call(False, 3600)[1], '')  # already told
        self.assertIn('failed 4 times', self.call(False, 1800 + REPORT_EVERY)[1])
        ok, report = self.call(True, 1800 + REPORT_EVERY + 1800)
        self.assertTrue(ok)
        self.assertEqual(report, 'Vault sync recovered after 4 failed attempts.')
        self.assertEqual(self.call(True, 1800 + REPORT_EVERY + 3600), (True, ''))


if __name__ == '__main__':
    unittest.main()
