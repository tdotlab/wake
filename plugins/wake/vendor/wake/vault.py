"""Vault sync: pull, commit and push the journal folder of a git-tracked vault, one caller at a time."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

from .core import atomic_write, file_lock

REPORT_AFTER = 2           # consecutive failures before saying anything (one blip stays quiet)
REPORT_EVERY = 24 * 3600   # while still failing, repeat at most this often

RETRYABLE = ('index.lock', 'Could not resolve host', 'Connection timed out', 'Connection reset',
             'unable to access', 'remote end hung up', 'cannot lock ref')


def no_window():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def default_vault():
    """The git-tracked folder journals are copied into: $WAKE_VAULT, else ~/vault."""
    return Path(os.environ.get('WAKE_VAULT') or Path.home() / 'vault')


def default_timezone():
    """The time zone that decides where one day ends: $WAKE_TIMEZONE (an IANA name), else UTC."""
    return os.environ.get('WAKE_TIMEZONE') or 'UTC'


def git(vault, *args):
    run = subprocess.run(['git', *args], cwd=vault, capture_output=True, text=True, encoding='utf-8',
                         errors='replace', timeout=300, **no_window())
    return run.returncode, (run.stdout + run.stderr).strip()


def sync_once(vault, message):
    """Pull, commit the journal folder and push. Returns (ok, output). Leaves the vault as it was on conflict."""
    log = []
    has_remote = bool(git(vault, 'remote')[1])
    if has_remote:
        code, out = git(vault, 'pull', '--rebase', '--autostash')
        log.append(out)
        if code != 0:
            git(vault, 'rebase', '--abort')
            return False, '\n'.join(log)
        # --autostash can exit 0 while its pop left conflicts behind; never commit those markers.
        unmerged = git(vault, 'ls-files', '--unmerged')[1]
        if unmerged:
            paths = sorted({line.split('\t', 1)[-1] for line in unmerged.splitlines()})
            log.append('Unresolved conflicts after pull (stashed local edits clashed with upstream): '
                       + ', '.join(paths) + '. Resolve them in the vault, then sync again.')
            return False, '\n'.join(log)
    git(vault, 'add', '-A', '--', 'journal')
    if git(vault, 'diff', '--cached', '--quiet')[0] != 0:
        code, out = git(vault, 'commit', '-m', message or 'wake: journal update')
        log.append(out)
        if code != 0:
            return False, '\n'.join(log)
    if has_remote:
        code, out = git(vault, 'push')
        log.append(out)
        if code != 0:
            return False, '\n'.join(log)
    return True, '\n'.join(x for x in log if x)


def sync(vault, message=None, attempts=3, wait=15):
    """Returns (ok, output). Never raises for git trouble; the next sync retries."""
    vault = Path(vault)
    if not (vault / '.git').is_dir():
        return False, f'{vault} is not a git clone'
    output = ''
    with file_lock(vault / '.git' / 'wake-sync.lock'):
        for attempt in range(attempts):
            try:
                ok, output = sync_once(vault, message)
            except subprocess.TimeoutExpired:
                output = 'git timed out after 300 seconds'
                continue
            if ok:
                return True, output
            if attempt + 1 < attempts and any(s in output for s in RETRYABLE):
                time.sleep(wait)
                continue
            break
    return False, output


def sync_quietly(vault, message=None, now=None, run=sync):
    """For timers: returns (ok, text to report or ''). Keeps a failure streak in .git so a stuck
    vault is reported once after REPORT_AFTER failures, then at most daily, and once on recovery."""
    now = time.time() if now is None else now
    state_path = Path(vault) / '.git' / 'wake-sync-state.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    ok, output = run(vault, message)
    if ok:
        if state.get('reported'):
            report = f"Vault sync recovered after {state['fails']} failed attempts."
        else:
            report = ''
        if state and state_path.parent.is_dir():
            state_path.unlink(missing_ok=True)
        return True, report
    state['fails'] = state.get('fails', 0) + 1
    state.setdefault('since', now)
    report = ''
    due = 'reported' not in state or now - state['reported'] >= REPORT_EVERY
    if state['fails'] >= REPORT_AFTER and due:
        since = time.strftime('%Y-%m-%d %H:%M', time.localtime(state['since']))
        report = (f"Vault sync on {Path(vault)} has failed {state['fails']} times in a row since {since}; "
                  f"notes there aren't reaching the remote.\n{output}")
        state['reported'] = now
    if state_path.parent.is_dir():
        atomic_write(state_path, json.dumps(state))
    return False, report
