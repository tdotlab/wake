"""Wake's opt-in, CLI-only Hermes integration. Registration is inert."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def run_wake(*args):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'vendor') + os.pathsep + env.get('PYTHONPATH', '')
    return subprocess.call([sys.executable, '-m', 'wake', *args], env=env)


def setup():
    print('Wake setup: each choice is optional. No schedules or settings are changed.')
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
        print('Memory maintenance is not implemented safely yet. No memory or jobs were changed.')
    return 0


def configure_parser(parser):
    sub = parser.add_subparsers(dest='wake_action', required=True)
    sub.add_parser('setup', help='Choose optional setup; defaults to no changes')
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
    opts = []
    for key in ('day', 'timezone', 'db', 'journal', 'vault'):
        value = getattr(args, key, None)
        if value is not None:
            opts += ['--' + key, value]
    return run_wake('journal', 'hermes', args.action, *opts, '--no-sync')


def register(ctx):
    ctx.register_cli_command(name='wake', help='Optional Wake vault and journal',
                             setup_fn=configure_parser, handler_fn=command)
