"""Wake's opt-in, CLI-only Hermes integration. Registration is inert."""
from pathlib import Path
import os
import json
import tempfile
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def run_wake(*args):
    bootstrap = "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module('wake', run_name='__main__')"
    return subprocess.call([sys.executable, '-I', '-B', '-c', bootstrap, str(ROOT / 'vendor'), *args])


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
    if not sys.stdin.isatty():
        print('Wake setup requires an interactive terminal.', file=sys.stderr)
        return 1
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
    journal = sub.add_parser('journal', help='Run a manual Hermes journal step')
    journal.add_argument('action', choices=['prepare', 'commit', 'status'])
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
