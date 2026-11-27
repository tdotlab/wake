"""Wake's opt-in, CLI-only Hermes integration. Registration is inert."""
from pathlib import Path
import os
import hashlib
import json
import uuid
import tempfile
import subprocess
import sys
import errno

ROOT = Path(__file__).resolve().parent


def _sync_directory(path):
    # Windows cannot open directories this way. Some filesystems do not support
    # directory fsync; other failures must abort before replacing memory.
    if os.name == 'nt':
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(fd)


def run_wake(*args):
    bootstrap = "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module('wake', run_name='__main__')"
    return subprocess.call([sys.executable, '-I', '-c', bootstrap, str(ROOT / 'vendor'), *args])



def _memory_snapshot(target):
    from tools.memory_tool_store import MemoryStore, ENTRY_DELIMITER
    if target not in ('memory', 'user'):
        raise ValueError('target must be memory or user')
    path = MemoryStore._path_for(target)
    if path.is_symlink() or not path.is_file():
        raise ValueError('memory file must be an existing regular non-symlink file')
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    entries = text.split(ENTRY_DELIMITER)
    if any(not e or e != e.strip() for e in entries):
        raise ValueError('noncanonical memory formatting; no changes made')
    distinct = list(dict.fromkeys(entries))
    return path, raw, distinct, len(entries) - len(distinct)


def memory_preview(target):
    path, raw, entries, duplicates = _memory_snapshot(target)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'duplicates': duplicates}


def memory_apply(target, expected_sha256):
    from tools.memory_tool_store import MemoryStore
    from tools import memory_tool
    if memory_tool.fcntl is None and memory_tool.msvcrt is None:
        raise RuntimeError('OS memory lock unavailable')
    path = MemoryStore._path_for(target) if target in ('memory', 'user') else None
    if path is None:
        raise ValueError('target must be memory or user')
    with MemoryStore._file_lock(path):
        path, raw, entries, duplicates = _memory_snapshot(target)
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError('memory changed since preview; preview again')
        if not duplicates:
            return {'removed': 0, 'backup': None}
        backup = path.with_name(path.name + '.wake-backup-' + uuid.uuid4().hex)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(backup, flags, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        if backup.read_bytes() != raw:
            raise OSError('backup verification failed; memory unchanged')
        _sync_directory(path.parent)
        MemoryStore._write_file(path, entries)
        return {'removed': duplicates, 'backup': str(backup)}


def connected_vault():
    from hermes_constants import get_hermes_home
    config = get_hermes_home() / 'wake' / 'config.json'
    if not config.exists():
        return None
    data = json.loads(config.read_text(encoding='utf-8'))
    return data.get('vault')


def connect_vault(path):
    from hermes_constants import get_hermes_home
    vault = Path(path).expanduser().resolve()
    if not vault.is_dir() or not (vault / 'AGENTS.md').is_file() or not (vault / 'hot.md').is_file():
        raise ValueError('existing vault must contain AGENTS.md and hot.md')
    folder = get_hermes_home() / 'wake'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.config-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'vault': str(vault)}, f)
        os.replace(temp, folder / 'config.json')
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return str(vault)


def setup():
    print('Wake setup: each choice is optional. No schedules are created.')
    if input('Create a new starter vault? [y/N] ').strip().lower() == 'y':
        path = input('New empty vault path: ').strip()
        if not path:
            print('No path supplied; vault not created.')
        elif input(f'Create starter files in {path!r} (no git commit)? [y/N] ').strip().lower() == 'y':
            result = run_wake('init', path, '--no-git')
            if result:
                return result
    if input('Show manual Hermes journal setup? [y/N] ').strip().lower() == 'y':
        print('Run hermes wake journal prepare --no-sync, then follow the bundled journal guide. '
              'No job is created. See the plugin README for the complete manual workflow.')
    if input('Would you like memory maintenance? [y/N] ').strip().lower() == 'y':
        print('Use hermes wake memory preview memory (or user), then apply with --sha256 after review. Only exact duplicate entries can be removed; no schedule was created.')
    if input('Connect an existing Wake vault and optionally install skills? [y/N] ').strip().lower() == 'y':
        path = input('Existing vault path: ').strip()
        if path and input(f'Connect {path!r} to this profile? [y/N] ').strip().lower() == 'y':
            try:
                vault = connect_vault(path)
            except (ValueError, OSError) as exc:
                print(f'Vault not connected: {exc}')
                return 1
            print(f'Connected vault: {vault}')
            if input('Install missing vault skills in this profile (no overwrite)? [y/N] ').strip().lower() == 'y':
                from hermes_constants import get_hermes_home
                skills = get_hermes_home() / 'skills'
                names = ('brain', 'brain-ingest', 'brain-lint')
                missing = [name for name in names if not (skills / name).exists()]
                status = run_wake('install-skills', '--hermes', '--vault', vault,
                                  *(arg for name in names for arg in ('--skill', name)))
                if status:
                    return status
                for name in missing:
                    skill = skills / name / 'SKILL.md'
                    if skill.is_file():
                        with skill.open('a', encoding='utf-8') as f:
                            f.write('\n## Hermes Wake profile connection\n\n'
                                    'For this Hermes profile, use the `vault` value in '
                                    '`$HERMES_HOME/wake/config.json` before the fallback paths above. '
                                    'If HERMES_HOME is unset, use `~/.hermes/wake/config.json`. '
                                    'Pass that path explicitly to helpers. If the connection is missing '
                                    'or invalid, ask the owner; do not guess another vault.\n')
                return 0
    return 0


def configure_parser(parser):
    sub = parser.add_subparsers(dest='wake_action', required=True)
    sub.add_parser('setup', help='Choose optional setup; defaults to no changes')
    memory = sub.add_parser('memory', help='Review exact-duplicate memory entries')
    memory.add_argument('action', choices=['preview', 'apply'])
    memory.add_argument('target', choices=['memory', 'user'])
    memory.add_argument('--sha256', help='Exact preview digest required to apply')
    journal = sub.add_parser('journal', help='Run a manual Hermes journal step')
    journal.add_argument('action', choices=['prepare', 'commit', 'status', 'evidence'])
    journal.add_argument('--day')
    journal.add_argument('--timezone')
    journal.add_argument('--db')
    journal.add_argument('--journal')
    journal.add_argument('--vault')
    journal.add_argument('--no-sync', action='store_true', help='Accepted for compatibility; sync is always disabled')


def command(args):
    if args.wake_action == 'setup':
        try:
            return setup()
        except (EOFError, KeyboardInterrupt):
            print('\nSetup cancelled.')
            return 1
    if args.wake_action == 'memory':
        try:
            if args.action == 'apply' and not args.sha256:
                raise ValueError('apply requires --sha256 from preview')
            result = memory_preview(args.target) if args.action == 'preview' else memory_apply(args.target, args.sha256)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except (ValueError, OSError, RuntimeError) as exc:
            print(f'Memory maintenance stopped: {exc}', file=sys.stderr)
            return 1
    opts = []
    for key in ('day', 'timezone', 'db', 'journal', 'vault'):
        value = getattr(args, key, None)
        if value is not None:
            opts += ['--' + key, value]
    if not getattr(args, 'vault', None):
        try:
            vault = connected_vault()
        except (OSError, ValueError) as exc:
            print(f'Cannot read connected vault: {exc}', file=sys.stderr)
            return 1
        if vault:
            opts += ['--vault', vault]
    return run_wake('journal', 'hermes', args.action, *opts, '--no-sync')


def register(ctx):
    ctx.register_cli_command(name='wake', help='Optional Wake vault and journal',
                             setup_fn=configure_parser, handler_fn=command)
