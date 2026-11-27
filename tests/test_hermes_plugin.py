"""Isolated contract tests for the optional Hermes plugin."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from test_hermes import SCHEMA, add, draft_for
from wake.core import day_bounds

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

    def test_missing_timezone_data_reports_install_hint(self):
        from zoneinfo import ZoneInfoNotFoundError
        mod = load_plugin()
        with patch('zoneinfo.ZoneInfo', side_effect=ZoneInfoNotFoundError('missing')):
            with self.assertRaisesRegex(ValueError, 'timezone data.*tzdata'):
                mod.validate_timezone('America/New_York')

    def test_picker_selects_a_discovered_provider_and_model(self):
        mod = load_plugin()
        rows = [{'slug': 'openrouter', 'label': 'OpenRouter', 'models': ['example/first', 'example/second']}]
        with patch.object(mod, '_discover_model_providers', return_value=rows), patch('builtins.input', side_effect=['2', '1', '2']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod._choose_journal_model(), ('example/second', 'openrouter'))

    def test_picker_allows_paging_to_later_models(self):
        mod = load_plugin()
        rows = [{'slug': 'example', 'models': [f'model-{i}' for i in range(1, 32)]}]
        with patch.object(mod, '_discover_model_providers', return_value=rows), patch('builtins.input', side_effect=['2', '1', 'n', '26']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod._choose_journal_model(), ('model-26', 'example'))

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
            with patch('builtins.input', side_effect=['y', dest, 'n', '', '', 'n']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.setup(), 0)
            self.assertFalse(Path(dest).exists())

    def test_vault_creation_with_explicit_confirmation(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            dest = Path(tmp) / 'new vault'
            with patch('builtins.input', side_effect=['y', str(dest), 'y', '', '', 'n']):
                self.assertEqual(mod.setup(), 0)
            self.assertTrue((dest / 'AGENTS.md').is_file())
            self.assertFalse((dest / '.git').exists())
            self.assertFalse((Path(tmp) / 'profile').exists())

    def test_register_exposes_cli_and_read_only_journal_skill(self):
        calls = []
        skills = []
        class Context:
            def register_cli_command(self, **kwargs): calls.append(kwargs)
            def register_skill(self, name, path, description='', frontmatter=None):
                skills.append((name, path))
        load_plugin().register(Context())
        self.assertEqual([c['name'] for c in calls], ['wake'])
        self.assertEqual([name for name, _ in skills], ['journal'])

    def test_setup_decline_everything_is_read_only(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            answers = iter(['', '', '', 'n'])
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
            with patch('builtins.input', side_effect=['', '', 'y', str(vault), 'y', 'y', 'n']), contextlib.redirect_stdout(io.StringIO()):
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
            with patch('builtins.input', side_effect=['', '', 'y', str(vault), 'y', 'y', 'n']), contextlib.redirect_stdout(io.StringIO()):
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

    def test_connect_vault_preserves_journal_settings(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            vault = Path(tmp) / 'vault'
            vault.mkdir()
            (vault / 'AGENTS.md').touch()
            (vault / 'hot.md').touch()
            (home / 'wake').mkdir(parents=True)
            (home / 'wake/config.json').write_text(json.dumps({
                'journal': {'time': '05:15', 'timezone': 'UTC', 'delivery': 'local'},
            }))
            mod.connect_vault(vault)
            data = json.loads((home / 'wake/config.json').read_text())
            self.assertEqual(data['vault'], str(vault.resolve()))
            self.assertEqual(data['journal']['time'], '05:15')

    def test_installed_brain_skill_knows_connected_profile_vault(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            vault = Path(tmp) / 'vault'
            vault.mkdir()
            (vault / 'AGENTS.md').touch()
            (vault / 'hot.md').touch()
            with patch('builtins.input', side_effect=['', '', 'y', str(vault), 'y', 'y', 'n']):
                self.assertEqual(mod.setup(), 0)
            text = (Path(tmp) / 'profile/skills/brain/SKILL.md').read_text()
            self.assertIn('wake/config.json', text)

class SchedulingTests(unittest.TestCase):
    def setUp(self):
        tty = patch('sys.stdin.isatty', return_value=True)
        tty.start()
        self.addCleanup(tty.stop)

    def _run_setup(self, mod, home, answers):
        with patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()) as out:
            code = mod.setup()
        return code, out.getvalue()

    def _jobs(self):
        from cron.jobs import load_jobs
        return load_jobs()

    def test_setup_defaults_to_creating_the_nightly_job(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            code, out = self._run_setup(mod, home, ['', '', '', '', '', '', '', '', ''])
            self.assertEqual(code, 0)
            jobs = self._jobs()
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            self.assertEqual(job['name'], 'Wake nightly journal')
            self.assertEqual(job['script'], 'wake_plugin_journal.py')
            self.assertEqual(job['skills'], ['wake:journal'])
            self.assertEqual(job['deliver'], 'local')
            self.assertEqual(job['schedule']['expr'], '30 0 * * *')
            self.assertTrue(job['enabled'])
            script = home / 'scripts/wake_plugin_journal.py'
            self.assertTrue(script.is_file())
            config = json.loads((home / 'wake/config.json').read_text())
            self.assertEqual(config['journal']['time'], '00:30')
            self.assertEqual(config['journal']['delivery'], 'local')
            self.assertIn('Scheduled: Wake nightly journal', out)
            self.assertIn(job['id'], out)

    def test_schedule_allows_pinning_a_discovered_model_and_provider(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            (Path(tmp) / 'profile' / 'wake').mkdir(parents=True)
            (Path(tmp) / 'profile' / 'wake/config.json').write_text(json.dumps({'vault': str(Path(tmp) / 'vault')}))
            rows = [{'slug': 'openrouter', 'models': ['meta/muse-spark-1.3-contributor']}]
            with patch.object(mod, '_discover_model_providers', return_value=rows):
                code, _ = self._run_setup(mod, Path(tmp) / 'profile', ['', '', '', '', '', '', '', '2', '1', '1'])
            self.assertEqual(code, 0)
            job = self._jobs()[0]
            self.assertEqual(job['model'], 'meta/muse-spark-1.3-contributor')
            self.assertEqual(job['provider'], 'openrouter')

    def test_setup_shows_that_fire_time_uses_scheduler_timezone_before_asking_for_time(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            answers = iter(['', '', '', '', '01:30', 'America/New_York', 'local', '', ''])
            seen = []
            with contextlib.redirect_stdout(io.StringIO()) as out:
                def answer(prompt):
                    if prompt.startswith('Nightly time'):
                        seen.append(out.getvalue())
                    return next(answers)
                with patch('builtins.input', side_effect=answer):
                    self.assertEqual(mod.setup(), 0)
            text = seen[0]
            self.assertIn('scheduler timezone', text.lower())
            self.assertIn('day boundary', text.lower())

    def test_setup_accepts_custom_time_timezone_and_delivery(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            code, _ = self._run_setup(mod, home, ['', '', '', '', '05:15', 'Europe/Berlin', 'telegram:12345', '', ''])
            self.assertEqual(code, 0)
            job = self._jobs()[0]
            self.assertEqual(job['schedule']['expr'], '15 5 * * *')
            self.assertEqual(job['deliver'], 'telegram:12345')
            config = json.loads((home / 'wake/config.json').read_text())
            self.assertEqual(config['journal'], {'time': '05:15', 'timezone': 'Europe/Berlin',
                                                'delivery': 'telegram:12345', 'job_id': job['id']})
            script = (home / 'scripts/wake_plugin_journal.py').read_text()
            self.assertIn('FALLBACK_PLUGIN', script)
            self.assertIn(str(PLUGIN), script)

    def test_setup_rejects_bad_timezone_without_writing_a_job(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = self._run_setup(mod, home, ['', '', '', '', '', 'Mars/Olympus'])[0]
            self.assertNotEqual(code, 0)
            self.assertEqual(self._jobs(), [])
            self.assertFalse((home / 'scripts/wake_plugin_journal.py').exists())

    def test_setup_keeps_an_existing_job_unless_update_is_explicit(self):
        mod = load_plugin()
        from cron.jobs import create_job
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            home.mkdir(parents=True)
            existing = create_job('old prompt', '0 1 * * *', name='Wake nightly journal',
                                  script='wake_plugin_journal.py', skills=['wake:journal'])
            mod._write_config({'journal': {'job_id': existing['id']}})
            code, out = self._run_setup(mod, home, ['', '', '', '', '', '', '', '', '', ''])
            self.assertEqual(code, 0)
            jobs = self._jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['id'], existing['id'])
            self.assertEqual(jobs[0]['prompt'], 'old prompt')
            self.assertEqual(jobs[0]['schedule']['expr'], '0 1 * * *')
            self.assertIn('left unchanged', out)

    def test_schedule_update_confirms_selected_model_and_provider(self):
        mod = load_plugin()
        from cron.jobs import create_job
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            existing = create_job('old prompt', '0 1 * * *', name='Wake nightly journal',
                                  script='wake_plugin_journal.py', skills=['wake:journal'])
            mod._write_config({'journal': {'job_id': existing['id']}})
            rows = [{'slug': 'example-provider', 'models': ['example/model']}]
            seen = []
            answers = iter(['', '', '', '', '', '', '', '2', '1', '1', '', 'n'])
            def answer(prompt):
                if prompt.startswith('Update it'):
                    seen.append(prompt)
                return next(answers)
            with patch.object(mod, '_discover_model_providers', return_value=rows), patch('builtins.input', side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.setup(), 0)
            self.assertIn('example/model', seen[0])
            self.assertIn('example-provider', seen[0])

    def test_setup_updates_an_existing_job_only_after_explicit_yes(self):
        mod = load_plugin()
        from cron.jobs import create_job
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            home.mkdir(parents=True)
            existing = create_job('old prompt', '0 1 * * *', name='Wake nightly journal',
                                  script='wake_plugin_journal.py', skills=['wake:journal'])
            mod._write_config({'journal': {'job_id': existing['id']}})
            code, _ = self._run_setup(mod, home, ['', '', '', '', '05:15', 'UTC', 'local', '', '', 'y'])
            self.assertEqual(code, 0)
            jobs = self._jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['id'], existing['id'])
            self.assertIn('wake:journal', jobs[0]['skills'])
            self.assertNotEqual(jobs[0]['prompt'], 'old prompt')
            self.assertEqual(jobs[0]['schedule']['expr'], '15 5 * * *')

    def test_setup_refuses_a_same_name_job_from_another_setup(self):
        mod = load_plugin()
        from cron.jobs import create_job
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            (home / 'wake').mkdir(parents=True)
            (home / 'wake/config.json').write_text(json.dumps({'vault': str(Path(tmp) / 'vault')}))
            create_job('other', '0 1 * * *', name='Wake nightly journal', script='other.py')
            code, out = self._run_setup(mod, home, ['', '', '', '', '', '', '', ''])
            self.assertNotEqual(code, 0)
            self.assertIn('not created by this setup', out)
            self.assertEqual(len(self._jobs()), 1)

    def test_unschedule_does_not_remove_same_name_unowned_job(self):
        mod = load_plugin()
        from cron.jobs import create_job
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            job = create_job('custom', '0 1 * * *', name='Wake nightly journal', script='other.py', skills=['wake:journal'])
            with contextlib.redirect_stdout(io.StringIO()):
                mod.unschedule()
            self.assertIn(job['id'], {j['id'] for j in self._jobs()})

    def test_unschedule_removes_only_the_wake_job(self):
        import argparse
        mod = load_plugin()
        from cron.jobs import create_job
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            home.mkdir(parents=True)
            wake = create_job('journal', '30 0 * * *', name='Wake nightly journal',
                              script='wake_plugin_journal.py', skills=['wake:journal'])
            mod._write_config({'journal': {'job_id': wake['id']}})
            other = create_job('other', '0 1 * * *', name='other job', script='wake_plugin_journal.py')
            parser = argparse.ArgumentParser()
            mod.configure_parser(parser)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.command(parser.parse_args(['unschedule'])), 0)
            ids = {job['id'] for job in self._jobs()}
            self.assertNotIn(wake['id'], ids)
            self.assertIn(other['id'], ids)

    def test_generated_script_gates_empty_day_then_commits_staged_draft(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            home.mkdir(parents=True)
            vault = Path(tmp) / 'vault'
            vault.mkdir()
            (vault / 'AGENTS.md').touch()
            (vault / 'hot.md').touch()
            (home / 'wake').mkdir(parents=True)
            (home / 'wake/config.json').write_text(json.dumps({
                'vault': str(vault),
                'journal': {'time': '00:30', 'timezone': 'UTC', 'delivery': 'local'},
            }))
            script = mod.write_prerun_script()
            self.assertTrue(script.is_file())
            db = home / 'state.db'
            sqlite3.connect(db).executescript(SCHEMA).connection.close()
            day = (datetime.now(dt_timezone.utc).date() - timedelta(days=1)).isoformat()
            env = dict(os.environ, HERMES_HOME=str(home), HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
            empty = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertEqual(empty.stdout.strip().splitlines()[-1], '{"wakeAgent": false}')
            c = sqlite3.connect(db)
            c.execute('INSERT INTO sessions VALUES(?,?)', ('s1', 'telegram'))
            add(c, 1, 's1', 'user', 'A synthetic day', day_bounds(day, 'UTC')[0] + 1)
            c.close()
            prepared = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            manifest = json.loads(prepared.stdout)
            self.assertEqual(manifest['day'], day)
            Path(manifest['draft_path']).write_text(draft_for(day, ['s:s1/m:1']))
            committed = subprocess.run([sys.executable, str(script), 'commit', '--day', day],
                                       capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(committed.returncode, 0, committed.stderr)
            note = home / 'journal' / f'{day}.md'
            self.assertTrue(note.is_file())
            self.assertEqual((vault / 'journal/hermes' / day[:7] / f'{day}.md').read_bytes(), note.read_bytes())

    def test_existing_script_with_different_content_is_not_overwritten(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            home = Path(tmp) / 'profile'
            (home / 'scripts').mkdir(parents=True)
            target = home / 'scripts/wake_plugin_journal.py'
            target.write_text('user script\n')
            with self.assertRaises(FileExistsError):
                mod.write_prerun_script()
            self.assertEqual(target.read_text(), 'user script\n')
            target.write_text('"""Wake nightly journal pre-run script (generated by `hermes wake setup`)."""\nold\n')
            self.assertEqual(mod.write_prerun_script(), target)
            self.assertIn('FALLBACK_PLUGIN', target.read_text())

    def test_readme_no_longer_claims_model_journal_or_scheduling_are_unavailable(self):
        text = (PLUGIN / 'README.md').read_text()
        self.assertNotIn('Model-authored journal flow is unavailable', text)
        self.assertNotIn('The plugin does not create journal schedules', text)
        self.assertTrue((PLUGIN / 'skills/journal/SKILL.md').is_file())


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

    def test_late_messages_update_an_unchanged_wake_vault_copy(self):
        from test_hermes import make_db
        from wake.core import day_bounds
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / 'vault'
            vault.mkdir()
            db = root / 'state.db'
            c = make_db(db)
            day = '2026-10-22'
            add(c, 1, 's1', 'user', 'first', day_bounds(day, 'UTC')[0] + 1)
            opts = ('--day', day, '--timezone', 'UTC', '--db', str(db), '--journal', str(root / 'journal'), '--vault', str(vault), '--no-sync')
            self.assertEqual(mod.run_wake('journal', 'hermes', 'prepare', *opts), 0)
            draft = root / 'journal/.staging' / day / 'draft.md'
            draft.write_text(draft_for(day, ['s:s1/m:1']))
            self.assertEqual(mod.run_wake('journal', 'hermes', 'commit', *opts), 0)
            target = vault / 'journal/hermes/2026-10' / f'{day}.md'
            previous = target.read_bytes()
            add(c, 2, 's1', 'user', 'late', day_bounds(day, 'UTC')[0] + 2)
            c.close()
            self.assertEqual(mod.run_wake('journal', 'hermes', 'prepare', *opts), 0)
            draft.write_text(draft_for(day, ['s:s1/m:2'], summary='Updated.'))
            self.assertEqual(mod.run_wake('journal', 'hermes', 'commit', *opts), 0)
            self.assertNotEqual(previous, target.read_bytes())
            self.assertEqual(target.read_bytes(), (root / 'journal' / f'{day}.md').read_bytes())
            target.write_text('owner edit')
            c = sqlite3.connect(db)
            add(c, 3, 's1', 'user', 'another late message', day_bounds(day, 'UTC')[0] + 3)
            c.close()
            self.assertEqual(mod.run_wake('journal', 'hermes', 'prepare', *opts), 0)
            draft.write_text(draft_for(day, ['s:s1/m:3'], summary='Updated again.'))
            self.assertNotEqual(mod.run_wake('journal', 'hermes', 'commit', *opts), 0)
            self.assertEqual(target.read_text(), 'owner edit')

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
            os.write(master, b'\n\n\nn\n')
            out, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, (out, err))
            self.assertIn(b'No journal job created', out)
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

    def test_setup_default_yes_creates_a_job_that_fires_and_skips_an_empty_day(self):
        import pty
        sqlite3.connect(self.home / 'state.db').executescript(SCHEMA).connection.close()
        master, slave = pty.openpty()
        proc = None
        try:
            proc = subprocess.Popen(['hermes', 'wake', 'setup'], stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
            os.close(slave)
            slave = None
            os.write(master, b'\n\n\n\n\n\n\n\n\n')
            out, err = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0, (out, err))
            self.assertIn(b'Scheduled: Wake nightly journal', out)
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.communicate()
            os.close(master)
            if slave is not None:
                os.close(slave)
        jobs = json.loads((self.home / 'cron/jobs.json').read_text())['jobs']
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job['script'], 'wake_plugin_journal.py')
        self.assertEqual(job['skills'], ['wake:journal'])
        run = subprocess.run(['hermes', 'cron', 'run', job['id']], env=self.env,
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(run.returncode, 0, run.stderr)
        outputs = list((self.home / 'cron/output' / job['id']).glob('*.md'))
        self.assertTrue(outputs)
        self.assertIn('wakeAgent=false', outputs[-1].read_text())
        self.assertFalse(any((self.home / 'journal').glob('*.md')))
        removed = subprocess.run(['hermes', 'wake', 'unschedule'], env=self.env,
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(json.loads((self.home / 'cron/jobs.json').read_text())['jobs'], [])
