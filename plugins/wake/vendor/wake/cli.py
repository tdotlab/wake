"""Command line for Wake: the starter vault and the nightly journals.

  init [DIR] [--owner NAME] [--merge]             create a starter vault (an Obsidian vault agents can use)
  brain lint|find|log|new|stats|sync              the vault helper (same code as _system/scripts/brain.py)
  install-skills [--claude] [--hermes] [--dir D]  copy the vault's agent skills where an agent looks for them
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
    vault = Path(args.vault) if args.vault else None
    vault_dir = vault / 'journal' / 'hermes' if vault else None
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


def init_command(args):
    from . import scaffold
    target = args.dir or default_vault()
    result = scaffold.init_vault(target, owner=args.owner, merge=args.merge, use_git=not args.no_git)
    print(f"Vault ready at {result['path']}: {len(result['created'])} files written"
          + (f", {len(result['skipped'])} already there and left alone" if result['skipped'] else '')
          + f". {result['git']}.")
    setting = (f'setx WAKE_VAULT "{result["path"]}"   (new shells only)' if os.name == 'nt'
               else f'export WAKE_VAULT="{result["path"]}"   (put it in your shell profile)')
    print(f"""
Next:
  1. Open the folder in Obsidian ("Open folder as vault"). Start at Home.md.
  2. Point your tools at it:   {setting}
  3. Give your agents the skills:   wake install-skills --claude --hermes
     Codex and others: tell them to read {result['path']}/AGENTS.md first.
  4. First chat: ask an agent to "read the brain, then interview me to fill in me/Profile.md".
  5. Optional nightly journals: see the Scheduling section of the README.""")


def install_skills_command(args):
    from . import scaffold
    destinations = scaffold.agent_skill_dirs(args.claude, args.hermes) + [Path(d) for d in args.dir or []]
    if not destinations:
        raise ValueError('say where: --claude, --hermes or --dir FOLDER')
    installed, skipped = scaffold.install_skills(destinations, vault=args.vault, owner=args.owner, force=args.force, selected=args.skill)
    for path in installed:
        print(f'installed {path}')
    for path in skipped:
        print(f'kept      {path} (already there; --force replaces it)')
    print('Agents find the vault through WAKE_VAULT (or ~/vault); set it if yours lives elsewhere.')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == 'brain':  # argparse mishandles a pass-through that starts with an option
        from . import brain
        return brain.main(argv[1:])
    parser = argparse.ArgumentParser(prog='wake', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    i = sub.add_parser('init', help='create a starter vault')
    i.add_argument('dir', nargs='?', help='where to create it (default: $WAKE_VAULT, else ~/vault)')
    i.add_argument('--owner', help="your name, written into the vault's rules (default: 'the owner')")
    i.add_argument('--merge', action='store_true', help='into a folder that already has files: add only what is missing')
    i.add_argument('--no-git', action='store_true', help='do not run git init')
    sub.add_parser('brain', help='vault helper: lint, find, log, new, stats, sync (see: wake brain --help)')
    s = sub.add_parser('install-skills', help="copy the vault's agent skills where an agent looks for them")
    s.add_argument('--claude', action='store_true', help='into ~/.claude/skills')
    s.add_argument('--hermes', action='store_true', help='into $HERMES_HOME/skills (default ~/.hermes/skills)')
    s.add_argument('--dir', action='append', help='into this folder (repeatable)')
    s.add_argument('--vault', help='take the skills from this vault (default: the one WAKE_VAULT points at)')
    s.add_argument('--owner', help='used only when installing the packaged copy')
    s.add_argument('--force', action='store_true', help='replace skills that are already installed')
    s.add_argument('--skill', action='append', choices=['brain', 'brain-ingest', 'brain-lint'], help='install only this skill (repeatable; fallback per skill)')
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
        if args.command == 'init':
            init_command(args)
        elif args.command == 'install-skills':
            install_skills_command(args)
        elif args.command == 'journal':
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
        parser.exit(1, f'{"Failed" if args.command in ("init", "install-skills") else "Journal failed"}: {error}\n')
    return 0
