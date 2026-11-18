"""Command line for the Wake journals.

  journal hermes prepare|commit|status|evidence   the Hermes journal (also run by the cron shim)
  journal claude-code prepare|commit|status       the Claude Code journal's steps, by hand
  cc-journal run [--max-days N]                   the nightly Claude Code run
  vault-sync [--quiet]                            pull, commit and push the journal folder of the vault
"""
from pathlib import Path
import argparse
import json
import os
import sqlite3
import sys

from .core import WAKE_OFF, DraftRejected, Journal, SourceChanged
from .vault import default_timezone, default_vault, sync, sync_quietly


def hermes_journal(args):
    from .sources.hermes import HermesSource
    home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
    db = args.db or home / 'state.db'
    vault = Path(args.vault) if args.vault else default_vault()
    vault_dir = vault / 'journal' / 'hermes' if (vault / '.git').is_dir() else None
    return Journal(HermesSource(db, args.timezone), args.journal or home / 'journal', vault_dir=vault_dir), vault


def cc_journal(args):
    from . import cc_runner
    vault = Path(args.vault) if args.vault else cc_runner.VAULT
    return cc_runner.make_journal(state_dir=args.journal or cc_runner.STATE_DIR, vault=vault), vault


def maybe_sync(journal, vault, args, label):
    if args.no_sync or not journal.published or not journal.vault_dir:
        return None
    ok, output = sync(vault, message=f"journal: {label} {', '.join(journal.published)}")
    return {'ok': ok, 'output': output.splitlines()[-3:]}


def journal_command(args):
    journal, vault = (hermes_journal if args.agent == 'hermes' else cc_journal)(args)
    log = lambda s: print(s, file=sys.stderr)
    if args.action == 'status':
        print(journal.status())
    elif args.action == 'commit':
        if not args.day:
            raise ValueError('commit requires --day')
        result = journal.commit(args.day)
        result['sync'] = maybe_sync(journal, vault, args, args.agent)
        print(json.dumps(result, indent=2))
    elif args.action == 'evidence':
        done = journal.rebuild_evidence([args.day] if args.day else None, log=log)
        sync_result = maybe_sync(journal, vault, args, f'{args.agent} evidence')
        print(json.dumps({'days': done, 'sync': sync_result}, indent=2))
    else:
        task = journal.prepare(args.day, log=log)
        if task:
            print(json.dumps(task, indent=2, ensure_ascii=False))
        else:
            result = maybe_sync(journal, vault, args, args.agent)  # only after a publish; empty days write nothing
            if result:
                log(f'Vault sync: {result}')
            print('NO_JOURNAL_WORK\n' + WAKE_OFF)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='wake', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    j = sub.add_parser('journal')
    j.add_argument('agent', choices=['hermes', 'claude-code'])
    j.add_argument('action', nargs='?', default='prepare', choices=['prepare', 'commit', 'status', 'evidence'])
    j.add_argument('--day')
    j.add_argument('--timezone', default=default_timezone())
    j.add_argument('--db', type=Path)
    j.add_argument('--journal', type=Path)
    j.add_argument('--vault')
    j.add_argument('--no-sync', action='store_true')
    r = sub.add_parser('cc-journal')
    r.add_argument('action', choices=['run'])
    r.add_argument('--max-days', type=int, default=3)
    r.add_argument('--vault')
    r.add_argument('--model', default='sonnet')
    r.add_argument('--effort', default='medium')
    v = sub.add_parser('vault-sync')
    v.add_argument('--vault', default=str(default_vault()))
    v.add_argument('--quiet', action='store_true',
                   help='for timers: print only when a failure streak starts, persists a day, or ends')
    v.add_argument('-m', '--message')
    args = parser.parse_args(argv)
    try:
        if args.command == 'journal':
            journal_command(args)
        elif args.command == 'cc-journal':
            from . import cc_runner
            ok = cc_runner.run(args.max_days, model_run=cc_runner.claude_runner(args.model, args.effort),
                               vault=args.vault or cc_runner.VAULT)
            return 0 if ok else 1
        elif args.quiet:
            ok, report = sync_quietly(args.vault, args.message)
            if report:
                print(report)
            return 0 if ok else 1
        else:
            ok, output = sync(args.vault, args.message)
            print(('' if ok else 'Vault sync failed: ') + output)
            return 0 if ok else 1
    except DraftRejected as error:
        parser.exit(1, f'Journal failed: {error}\n')
    except (SourceChanged, ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'Journal failed: {error}\n')
    return 0
