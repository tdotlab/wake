"""Isolated contract tests for the optional Hermes plugin."""
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins' / 'wake'

def load_plugin():
    spec = importlib.util.spec_from_file_location('wake_hermes_plugin', PLUGIN / '__init__.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PluginTests(unittest.TestCase):
    def setUp(self):
        tty = patch('sys.stdin.isatty', return_value=True)
        tty.start()
        self.addCleanup(tty.stop)

    def test_setup_rejects_noninteractive_stdin_before_prompting(self):
        mod = load_plugin()
        with patch('sys.stdin', io.StringIO('')), patch('builtins.input') as prompt:
            self.assertNotEqual(mod.setup(), 0)
        prompt.assert_not_called()

    def test_evidence_bulk_rewrite_not_exposed(self):
        import argparse
        parser = argparse.ArgumentParser()
        load_plugin().configure_parser(parser)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['journal', 'evidence'])

    def test_vault_init_does_not_advertise_unsupported_plugin_scheduling(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            vendor = PLUGIN / 'vendor'
            result = subprocess.run(
                [sys.executable, '-I', '-c',
                 "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module('wake', run_name='__main__')",
                 str(vendor), 'init', str(Path(tmp) / 'vault'), '--no-git'],
                text=True, capture_output=True, check=True)
            self.assertNotIn('Optional nightly journals', result.stdout)

    def test_hermes_dependency_declaration_retains_windows_tzdata_marker(self):
        from pm.plugin_declarations import read_python_declaration
        from packaging.requirements import Requirement
        declarations = read_python_declaration(PLUGIN).requirements
        tzdata = [Requirement(item) for item in declarations if Requirement(item).name == 'tzdata']
        self.assertEqual(len(tzdata), 1)
        self.assertTrue(tzdata[0].marker.evaluate({'sys_platform': 'win32'}))
        self.assertFalse(tzdata[0].marker.evaluate({'sys_platform': 'linux'}))

    def test_memory_command_is_not_exposed(self):
        import argparse
        parser = argparse.ArgumentParser()
        load_plugin().configure_parser(parser)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['memory', 'preview', 'memory'])

    def test_vault_creation_requires_second_confirmation(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            dest = str(Path(tmp) / 'new vault')
            with patch('builtins.input', side_effect=['y', dest, 'n', '', '', '']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.setup(), 0)
            self.assertFalse(Path(dest).exists())

    def test_vault_creation_with_explicit_confirmation(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            dest = Path(tmp) / 'new vault'
            with patch('builtins.input', side_effect=['y', str(dest), 'y', '', '', '']):
                self.assertEqual(mod.setup(), 0)
            self.assertTrue((dest / 'AGENTS.md').is_file())
            self.assertFalse((dest / '.git').exists())
            self.assertFalse((Path(tmp) / 'profile').exists())

    def test_register_exposes_regular_cli_only(self):
        calls = []
        class Context:
            def register_cli_command(self, **kwargs): calls.append(kwargs)
        load_plugin().register(Context())
        self.assertEqual([c['name'] for c in calls], ['wake'])

    def test_setup_decline_everything_is_read_only(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            answers = iter(['', '', ''])
            prompts = []
            def reply(prompt):
                prompts.append(prompt)
                return next(answers)
            with patch('builtins.input', side_effect=reply), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.setup(), 0)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertFalse(any('memory' in prompt.lower() for prompt in prompts))

class ExistingVaultTests(unittest.TestCase):
    def setUp(self):
        tty = patch('sys.stdin.isatty', return_value=True)
        tty.start()
        self.addCleanup(tty.stop)

    def test_setup_installs_only_three_skills_with_per_skill_fallback(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            vault = Path(tmp) / 'vault'
            (vault / '_system/skills/brain').mkdir(parents=True)
            (vault / 'AGENTS.md').touch()
            (vault / 'hot.md').touch()
            (vault / '_system/skills/brain/SKILL.md').write_text('custom brain')
            (vault / '_system/skills/unrelated').mkdir()
            (vault / '_system/skills/unrelated/SKILL.md').write_text('not authorized')
            with patch('builtins.input', side_effect=['', '', 'y', str(vault), 'y', 'y']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.setup(), 0)
            skills = Path(tmp) / 'profile/skills'
            self.assertEqual({p.name for p in skills.iterdir()}, {'brain', 'brain-ingest', 'brain-lint'})
            self.assertIn('custom brain', (skills / 'brain/SKILL.md').read_text())
            for name in ('brain', 'brain-ingest', 'brain-lint'):
                self.assertIn('wake/config.json', (skills / name / 'SKILL.md').read_text())

    def test_connect_existing_vault_and_install_skills_without_overwrite(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            vault = Path(tmp) / 'existing vault'
            vault.mkdir()
            (vault / 'AGENTS.md').write_text('existing rules')
            (vault / 'hot.md').write_text('existing hot')
            dest = Path(tmp) / 'profile' / 'skills' / 'brain'
            dest.mkdir(parents=True)
            (dest / 'SKILL.md').write_text('do not replace')
            with patch('builtins.input', side_effect=['', '', 'y', str(vault), 'y', 'y']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.setup(), 0)
            self.assertEqual(mod.connected_vault(), str(vault))
            self.assertEqual((dest / 'SKILL.md').read_text(), 'do not replace')
            self.assertTrue((dest.parent / 'brain-ingest' / 'SKILL.md').is_file())
            self.assertEqual((vault / 'AGENTS.md').read_text(), 'existing rules')

    def test_connected_vault_is_passed_to_journal(self):
        import argparse
        mod = load_plugin()
        parser = argparse.ArgumentParser()
        mod.configure_parser(parser)
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HERMES_HOME': tmp}):
            vault = Path(tmp) / 'vault'
            vault.mkdir()
            (vault / 'AGENTS.md').touch()
            (vault / 'hot.md').touch()
            mod.connect_vault(vault)
            with patch.object(mod, 'run_wake', return_value=0) as run:
                self.assertEqual(mod.command(parser.parse_args(['journal', 'status'])), 0)
            self.assertIn(str(vault), run.call_args.args)

    def test_installed_brain_skill_knows_connected_profile_vault(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            vault = Path(tmp) / 'vault'
            vault.mkdir()
            (vault / 'AGENTS.md').touch()
            (vault / 'hot.md').touch()
            with patch('builtins.input', side_effect=['', '', 'y', str(vault), 'y', 'y']):
                self.assertEqual(mod.setup(), 0)
            text = (Path(tmp) / 'profile/skills/brain/SKILL.md').read_text()
            self.assertIn('wake/config.json', text)

class SecurityRegressionTests(unittest.TestCase):
    def test_subprocess_ignores_hostile_working_directory(self):
        import os
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path.cwd()
            try:
                os.chdir(tmp)
                Path('wake.py').write_text("from pathlib import Path\nPath('pwned').touch()\n")
                self.assertEqual(mod.run_wake('--help'), 0)
                self.assertFalse(Path('pwned').exists())
            finally:
                os.chdir(cwd)

    def test_starter_vault_requires_approval_before_git_sync(self):
        text = (PLUGIN / 'vendor/wake/starter/AGENTS.md').read_text()
        skill = (PLUGIN / 'vendor/wake/starter/_system/skills/brain/SKILL.md').read_text()
        self.assertIn('explicit approval', text.lower())
        self.assertIn('explicit approval', skill.lower())

    def test_journal_conflict_preserves_note_and_pending_draft(self):
        from test_hermes import make_db, add, draft_for
        from wake.core import day_bounds
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / 'vault'
            (vault / '.git').mkdir(parents=True)
            target = vault / 'journal/hermes/2026-10/2026-10-22.md'
            target.parent.mkdir(parents=True)
            target.write_text('owner note')
            old = target.parent.parent / '2026-10-22.md'
            old.write_text('old owner note')
            db = root / 'state.db'
            c = make_db(db)
            add(c, 1, 's1', 'user', 'hello', day_bounds('2026-10-22', 'UTC')[0] + 1)
            c.close()
            opts = ('--day', '2026-10-22', '--timezone', 'UTC', '--db', str(db), '--journal', str(root / 'journal'), '--vault', str(vault), '--no-sync')
            self.assertEqual(mod.run_wake('journal', 'hermes', 'prepare', *opts), 0)
            draft = root / 'journal/.staging/2026-10-22/draft.md'
            draft.write_text(draft_for('2026-10-22', ['s:s1/m:1']))
            self.assertNotEqual(mod.run_wake('journal', 'hermes', 'commit', *opts), 0)
            self.assertEqual(target.read_text(), 'owner note')
            self.assertEqual(old.read_text(), 'old owner note')
            self.assertTrue(draft.exists())
            import json
            state = json.loads((root / 'journal/.state.json').read_text())
            self.assertNotIn('2026-10-22', state['days'])

    def test_unconnected_journal_ignores_environment_vault(self):
        import argparse
        from test_hermes import make_db, add, draft_for
        from wake.core import day_bounds
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HERMES_HOME': tmp + '/profile', 'WAKE_VAULT': tmp + '/vault'}):
            root = Path(tmp)
            (root / 'vault/.git').mkdir(parents=True)
            db = root / 'state.db'
            c = make_db(db)
            add(c, 1, 's1', 'user', 'hello', day_bounds('2026-10-22', 'UTC')[0] + 1)
            c.close()
            parser = argparse.ArgumentParser()
            mod.configure_parser(parser)
            opts = ['--day', '2026-10-22', '--timezone', 'UTC', '--db', str(db), '--journal', str(root / 'journal')]
            self.assertEqual(mod.command(parser.parse_args(['journal', 'prepare', *opts])), 0)
            (root / 'journal/.staging/2026-10-22/draft.md').write_text(draft_for('2026-10-22', ['s:s1/m:1']))
            self.assertEqual(mod.command(parser.parse_args(['journal', 'commit', *opts])), 0)
            self.assertFalse((root / 'vault/journal').exists())

    def test_explicit_nongit_vault_receives_first_journal_copy(self):
        from test_hermes import make_db, add, draft_for
        from wake.core import day_bounds
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / 'vault'
            vault.mkdir()
            db = root / 'state.db'
            c = make_db(db)
            add(c, 1, 's1', 'user', 'hello', day_bounds('2026-10-22', 'UTC')[0] + 1)
            c.close()
            opts = ('--day', '2026-10-22', '--timezone', 'UTC', '--db', str(db), '--journal', str(root / 'journal'), '--vault', str(vault), '--no-sync')
            self.assertEqual(mod.run_wake('journal', 'hermes', 'prepare', *opts), 0)
            (root / 'journal/.staging/2026-10-22/draft.md').write_text(draft_for('2026-10-22', ['s:s1/m:1']))
            self.assertEqual(mod.run_wake('journal', 'hermes', 'commit', *opts), 0)
            self.assertEqual((vault / 'journal/hermes/2026-10/2026-10-22.md').read_bytes(), (root / 'journal/2026-10-22.md').read_bytes())


@unittest.skipUnless(os.name == 'posix' and shutil.which('hermes'), 'requires POSIX and Hermes CLI')
class RealHermesCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'plugins').mkdir()
        self.plugin = self.home / 'plugins/wake'
        shutil.copytree(PLUGIN, self.plugin, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (self.home / 'config.yaml').write_text('plugins:\n  enabled:\n    - wake\n')
        self.env = {key: os.environ[key] for key in ('PATH', 'HERMES_SITE') if key in os.environ}
        self.env.update(HOME=str(self.home), HERMES_HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1')

    def test_setup_silent_open_pipe_exits_without_writes(self):
        proc = subprocess.Popen(['hermes', 'wake', 'setup'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        try:
            code = proc.wait(timeout=120)  # stdin remains open and silent
            output = proc.stderr.read().decode()
            self.assertNotEqual(code, 0)
            self.assertIn('requires an interactive terminal', output)
            self.assertFalse((self.home / 'wake').exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()

    def test_setup_via_pty_decline_and_subprocess_no_bytecode(self):
        import pty
        master, slave = pty.openpty()
        proc = None
        try:
            proc = subprocess.Popen(['hermes', 'wake', 'setup'], stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
            os.close(slave)
            slave = None
            os.write(master, b'\n\n\n')
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, (out, err))
            self.assertIn(b'No schedules are created', out)
            self.assertFalse((self.home / 'wake').exists())
            # Exercise the isolated Wake subprocess from the real CLI too.
            help_result = subprocess.run(['hermes', 'wake', 'journal', 'status', '--journal', str(self.home / 'journal')],
                                         env=self.env, capture_output=True, timeout=120)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertEqual(list(self.plugin.rglob('*.pyc')), [])
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.communicate()
            os.close(master)
            if slave is not None:
                os.close(slave)
