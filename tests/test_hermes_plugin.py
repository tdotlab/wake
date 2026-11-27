"""Isolated contract tests for the optional Hermes plugin."""
import contextlib
import importlib.util
import io
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
    def test_memory_opt_in_fails_closed(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            with patch('builtins.input', side_effect=['', '', 'y']), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(mod.setup(), 0)
            self.assertIn('not implemented', output.getvalue())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_vault_creation_requires_second_confirmation(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            dest = str(Path(tmp) / 'new vault')
            with patch('builtins.input', side_effect=['y', dest, 'n', '', '']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mod.setup(), 0)
            self.assertFalse(Path(dest).exists())

    def test_vault_creation_with_explicit_confirmation(self):
        mod = load_plugin()
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'HOME': tmp, 'HERMES_HOME': tmp + '/profile'}):
            dest = Path(tmp) / 'new vault'
            with patch('builtins.input', side_effect=['y', str(dest), 'y', '', '']):
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
            self.assertTrue(any('memory' in prompt.lower() for prompt in prompts))
