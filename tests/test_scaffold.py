"""`wake init` and `wake install-skills`: layout, placeholders, refusal to overwrite, and a scan that
keeps the starter free of anyone's personal details."""
import _setup  # noqa: F401
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
import io
import json
import os
import re
import subprocess
import tempfile
import unittest

from wake import cli, scaffold

IDENTITY = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
            'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, IDENTITY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


class InitTests(TempCase):
    def test_layout_dotfiles_and_gitkeeps(self):
        vault = self.root / 'v'
        result = scaffold.init_vault(vault, owner='Sam Example', today='2026-03-04')
        for rel in ('AGENTS.md', 'CLAUDE.md', 'README.md', 'Home.md', 'hot.md', 'index.md', 'log.md',
                    '.gitignore', '.gitattributes', '.obsidian/app.json', '.obsidian/templates.json',
                    'me/Profile.md', 'projects/Projects.base', 'wiki/Wiki.base',
                    '_system/templates/Project.md', '_system/scripts/brain.py',
                    '_system/skills/brain/SKILL.md', '_system/skills/brain-ingest/SKILL.md',
                    '_system/skills/brain-lint/SKILL.md'):
            self.assertTrue((vault / rel).is_file(), rel)
        for folder in scaffold.EMPTY_DIRS:
            self.assertTrue((vault / folder / '.gitkeep').is_file(), folder)
        self.assertFalse(any(p.name.startswith('dot-') for p in vault.rglob('*')))
        self.assertEqual((vault / 'CLAUDE.md').read_text(encoding='utf-8').strip(), '@AGENTS.md')
        self.assertIn('one commit', result['git'])
        self.assertEqual(subprocess.run(['git', 'log', '--oneline'], cwd=vault, capture_output=True, text=True).stdout.count('\n'), 1)

    def test_placeholders_filled_at_init_and_left_for_new(self):
        vault = self.root / 'v'
        scaffold.init_vault(vault, owner='Sam Example', today='2026-03-04')
        everything = '\n'.join(p.read_text(encoding='utf-8') for p in vault.rglob('*.md') if '_system' not in p.parts)
        self.assertNotIn('{{owner}}', everything)
        self.assertNotIn('{{today}}', everything)
        self.assertIn('Sam Example', (vault / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertIn('2026-03-04', (vault / 'hot.md').read_text(encoding='utf-8'))
        template = (vault / '_system' / 'templates' / 'Project.md').read_text(encoding='utf-8')
        self.assertIn('{{title}}', template)
        self.assertIn('{{date}}', template)

    def test_default_owner_when_none_given(self):
        vault = self.root / 'v'
        scaffold.init_vault(vault)
        self.assertIn('the owner', (vault / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_refuses_nonempty_folder_without_merge(self):
        vault = self.root / 'v'
        vault.mkdir()
        (vault / 'mine.md').write_text('keep', encoding='utf-8')
        with self.assertRaises(ValueError):
            scaffold.init_vault(vault)
        self.assertEqual(sorted(p.name for p in vault.iterdir()), ['mine.md'])

    def test_merge_adds_missing_files_and_never_overwrites(self):
        vault = self.root / 'v'
        vault.mkdir()
        (vault / 'hot.md').write_text('my own hot', encoding='utf-8')
        (vault / 'notes').mkdir()
        (vault / 'notes' / 'x.md').write_text('mine', encoding='utf-8')
        result = scaffold.init_vault(vault, merge=True, use_git=False)
        self.assertEqual((vault / 'hot.md').read_text(encoding='utf-8'), 'my own hot')
        self.assertIn('hot.md', result['skipped'])
        self.assertTrue((vault / 'index.md').is_file())
        self.assertFalse((vault / 'notes' / '.gitkeep').exists())  # a folder with content needs no placeholder
        self.assertFalse((vault / '.git').exists())

    def test_existing_git_repo_is_left_alone(self):
        vault = self.root / 'v'
        vault.mkdir()
        subprocess.run(['git', 'init', '-b', 'main'], cwd=vault, capture_output=True, check=True)
        result = scaffold.init_vault(vault, merge=True)
        self.assertIn('already a git repository', result['git'])

    def test_a_file_where_the_folder_should_be_is_an_error(self):
        target = self.root / 'file'
        target.write_text('x', encoding='utf-8')
        with self.assertRaises(ValueError):
            scaffold.init_vault(target)

    def test_json_configs_parse(self):
        vault = self.root / 'v'
        scaffold.init_vault(vault, use_git=False)
        for path in (vault / '.obsidian').glob('*.json'):
            json.loads(path.read_text(encoding='utf-8'))

    def test_cli_init_and_failure_message(self):
        vault = self.root / 'v'
        code, out = run_cli('init', str(vault), '--owner', 'Sam Example', '--no-git')
        self.assertEqual(code, 0, out)
        self.assertIn('Vault ready', out)
        self.assertFalse((vault / '.git').exists())
        with mock.patch('sys.stderr', io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            run_cli('init', str(vault))
        self.assertEqual(caught.exception.code, 1)
        self.assertIn('not empty', err.getvalue())

    def test_cli_brain_passthrough_uses_vault_option(self):
        vault = self.root / 'v'
        scaffold.init_vault(vault, use_git=False)
        code, out = run_cli('brain', '--vault', str(vault), 'lint', '--strict')
        self.assertEqual(code, 0, out)
        self.assertIn('Dead links: none', out)


class InstallSkillsTests(TempCase):
    def setUp(self):
        super().setUp()
        self.vault = self.root / 'v'
        scaffold.init_vault(self.vault, use_git=False)

    def test_copies_skip_existing_and_force_overwrites(self):
        dest = self.root / 'agent' / 'skills'
        installed, skipped = scaffold.install_skills([dest], vault=self.vault)
        self.assertEqual(len(installed), 3)
        self.assertEqual(skipped, [])
        self.assertTrue((dest / 'brain' / 'SKILL.md').is_file())
        (dest / 'brain' / 'SKILL.md').write_text('edited', encoding='utf-8')
        installed, skipped = scaffold.install_skills([dest], vault=self.vault)
        self.assertEqual(installed, [])
        self.assertEqual(len(skipped), 3)
        self.assertEqual((dest / 'brain' / 'SKILL.md').read_text(encoding='utf-8'), 'edited')
        installed, _ = scaffold.install_skills([dest], vault=self.vault, force=True)
        self.assertEqual(len(installed), 3)
        self.assertIn('name: brain', (dest / 'brain' / 'SKILL.md').read_text(encoding='utf-8'))

    def test_prefers_the_vaults_own_edited_skills(self):
        skill = self.vault / '_system' / 'skills' / 'brain' / 'SKILL.md'
        skill.write_text(skill.read_text(encoding='utf-8') + '\nOWNER EDIT\n', encoding='utf-8')
        dest = self.root / 'out'
        scaffold.install_skills([dest], vault=self.vault)
        self.assertIn('OWNER EDIT', (dest / 'brain' / 'SKILL.md').read_text(encoding='utf-8'))

    def test_falls_back_to_packaged_skills_without_a_vault(self):
        empty = self.root / 'empty'
        empty.mkdir()
        dest = self.root / 'out'
        installed, _ = scaffold.install_skills([dest], vault=empty)
        self.assertEqual(len(installed), 3)

    def test_agent_dirs_honour_hermes_home(self):
        with mock.patch.dict(os.environ, {'HERMES_HOME': str(self.root / 'h')}):
            dirs = scaffold.agent_skill_dirs(claude=True, hermes=True)
        self.assertIn(self.root / 'h' / 'skills', dirs)
        self.assertEqual(scaffold.agent_skill_dirs(), [])

    def test_cli_install_skills_to_dir(self):
        dest = self.root / 'out'
        code, out = run_cli('install-skills', '--dir', str(dest), '--vault', str(self.vault))
        self.assertEqual(code, 0, out)
        self.assertTrue((dest / 'brain-lint' / 'SKILL.md').is_file())

    def test_cli_install_skills_with_no_destination_is_an_error(self):
        with mock.patch('sys.stderr', io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            run_cli('install-skills', '--vault', str(self.vault))
        self.assertEqual(caught.exception.code, 1)
        self.assertIn('say where', err.getvalue())


class StarterHygieneTests(unittest.TestCase):
    """The starter ships to strangers. Nothing about its author may leak into it."""
    FORBIDDEN = [r'ringd', r'C:\\Users', r'/home/\w+', r'Quentin', r'Toy Perro', r'\bPip\b', r"\bT's\b",
                 r'\bMike\b', r'notion\.so', r'discord\.com/api/webhooks']

    def test_no_personal_strings_in_starter_or_helpers(self):
        files = [p for p in scaffold.STARTER.rglob('*') if p.is_file()]
        files += [Path(scaffold.__file__), Path(scaffold.brain.__file__)]
        self.assertGreater(len(files), 25)
        for path in files:
            text = path.read_text(encoding='utf-8')
            for pattern in self.FORBIDDEN:
                self.assertIsNone(re.search(pattern, text), f'{path.name} matches {pattern}')

    def test_every_starter_text_file_is_valid_utf8_with_lf_endings(self):
        for path in scaffold.STARTER.rglob('*'):
            if path.is_file():
                data = path.read_bytes()
                data.decode('utf-8')
                self.assertNotIn(b'\r\n', data, path.name)

    def test_skills_have_frontmatter_names_matching_their_folder(self):
        for skill in (scaffold.STARTER / '_system' / 'skills').iterdir():
            text = (skill / 'SKILL.md').read_text(encoding='utf-8')
            self.assertTrue(text.startswith('---\n'))
            self.assertIn(f'name: {skill.name}\n', text)


if __name__ == '__main__':
    unittest.main()
