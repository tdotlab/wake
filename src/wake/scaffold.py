"""`wake init` (create a starter vault) and `wake install-skills` (copy the vault's agent skills where agents look)."""
from datetime import date
from pathlib import Path
import os
import shutil
import subprocess

from . import brain

STARTER = Path(__file__).resolve().parent / 'starter'
DEFAULT_OWNER = 'the owner'
# Folders a fresh vault should have even though the starter has nothing to put in them yet.
EMPTY_DIRS = ('inbox', 'raw/assets', 'daily', 'notes', 'journal', 'people', 'archive',
              'wiki/entities', 'wiki/concepts', 'wiki/sources', 'wiki/answers')
TEXT_SUFFIXES = {'.md', '.json', '.base', '.txt', ''}  # '' covers .gitignore and friends
NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def render(text, owner, today):
    """Fill the init-time placeholders. {{title}} and {{date}} are left for `brain.py new` to fill later."""
    return text.replace('{{owner}}', owner).replace('{{today}}', today)


def starter_name(name):
    """Package data cannot hold dotfiles reliably, so the starter spells a leading dot as `dot-`."""
    return '.' + name[4:] if name.startswith('dot-') else name


def starter_files():
    for path in sorted(STARTER.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            rel = path.relative_to(STARTER)
            yield path, Path(*[starter_name(part) for part in rel.parts])


def write_file(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def run_git(folder, *args):
    return subprocess.run(['git', *args], cwd=folder, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', **NO_WINDOW)


def init_vault(target, owner=None, merge=False, use_git=True, today=None):
    """Create the starter vault in `target`. Returns {'created': [...], 'skipped': [...], 'git': str}.
    Refuses a folder that already has files unless `merge`; even then it never overwrites a file."""
    target = Path(target).expanduser().resolve()
    owner = (owner or '').strip() or DEFAULT_OWNER
    today = today or date.today().isoformat()
    if target.exists() and not target.is_dir():
        raise ValueError(f'{target} exists and is not a folder')
    if target.is_dir() and any(target.iterdir()) and not merge:
        raise ValueError(f'{target} is not empty. Pick a new folder, or pass --merge to add only the files '
                         f'that are missing (nothing existing is overwritten).')
    created, skipped = [], []

    def place(rel, data):
        dest = target / rel
        if dest.exists():
            skipped.append(rel.as_posix())
        else:
            write_file(dest, data)
            created.append(rel.as_posix())

    for source, rel in starter_files():
        raw = source.read_bytes()
        if source.suffix in TEXT_SUFFIXES:
            raw = render(raw.decode('utf-8'), owner, today).encode('utf-8')
        place(rel, raw)
    place(Path('_system/scripts/brain.py'), Path(brain.__file__).read_bytes())
    for folder in EMPTY_DIRS:
        directory = target / folder
        if not directory.is_dir() or not any(directory.iterdir()):
            place(Path(folder) / '.gitkeep', b'')
    result = {'path': str(target), 'created': created, 'skipped': skipped, 'git': ''}
    if use_git:
        result['git'] = git_init(target)
    return result


def git_init(target):
    if (target / '.git').exists():
        return 'already a git repository; nothing committed'
    if run_git(target, 'init', '-b', 'main').returncode != 0:
        return 'git init failed (is git installed?); the vault works without it'
    run_git(target, 'add', '-A')
    commit = run_git(target, 'commit', '-m', 'wake: starter vault')
    if commit.returncode != 0:
        return ('git repository created, first commit skipped (set user.name and user.email, then '
                '`git add -A && git commit`)')
    return 'git repository created with one commit'


def skills_source(vault=None):
    """Prefer the skills in the vault (the owner may have edited them); else the packaged starter copy."""
    local = brain.find_vault(vault) / '_system' / 'skills'
    return local if local.is_dir() else STARTER / '_system' / 'skills'


def install_skills(destinations, vault=None, owner=None, force=False, today=None):
    """Copy each skill folder into every destination. Returns (installed, skipped) path lists."""
    source = skills_source(vault)
    owner = (owner or '').strip() or DEFAULT_OWNER
    today = today or date.today().isoformat()
    installed, skipped = [], []
    for destination in destinations:
        for skill in sorted(p for p in source.iterdir() if p.is_dir()):
            out = Path(destination).expanduser() / skill.name
            if out.exists() and not force:
                skipped.append(str(out))
                continue
            if out.exists():
                shutil.rmtree(out)
            for file in skill.rglob('*'):
                if file.is_file():
                    data = file.read_bytes()
                    if file.suffix in TEXT_SUFFIXES:
                        data = render(data.decode('utf-8'), owner, today).encode('utf-8')
                    write_file(out / file.relative_to(skill), data)
            installed.append(str(out))
    return installed, skipped


def agent_skill_dirs(claude=False, hermes=False):
    dirs = []
    if claude:
        dirs.append(Path.home() / '.claude' / 'skills')
    if hermes:
        dirs.append(Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes') / 'skills')
    return dirs
