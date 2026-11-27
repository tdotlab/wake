"""Shared journal machinery: staging, draft validation, the Evidence section, publishing, status.

A source (sources/hermes.py, sources/claude_code.py) turns one local calendar day of an agent's
conversations into a list of messages. The Journal stages them for a model, validates the draft the
model writes, appends a script-built Evidence section, and publishes the note to the agent's journal
folder and, when configured, to the vault.

Message fields every source provides:
  ref       citation id, unique within the day ("s:<session>/m:<id>", "c:<session8>/u:<uuid8>")
  role      user | assistant | tool (a tool result; staged as evidence, not narrative) |
            call (tool calls the agent made; rendered as compact lines) | event (notices, interrupts)
  speaker   owner (the human's own words) | agent | tool | other (subagent briefs, peers, notices)
  source    where it happened (telegram, cli, a Claude Code project name ...)
  time      local HH:MM;  timestamp: epoch seconds
  content   redacted, possibly truncated text;  truncated: bool;  call: optional tool call line
"""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import contextlib
import json
import os
import re
import shutil
import textwrap
import tempfile
import uuid

REOPEN_DAYS = 3
PART_CHARS = 30000
SUMMARY_LIMIT = 900
DRAFT_LIMIT = 40000
CALL_LINE_LIMIT = 300
# Evidence clips only what staging already clipped (12,000 is the largest staging cap).
EXCERPT_LIMIT = 12000
SECTIONS = ['Summary', 'Decisions and corrections', 'Work and outcomes', 'Open threads',
            'Coverage and uncertainty']
WAKE_OFF = '{"wakeAgent": false}'  # Hermes cron: skip the agent run entirely
DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
TRUNCATION_MARK = '\n[EXCERPT TRUNCATED; consult source transcript]\n'
# Pasted blocks are kept in the narrative between these markers so Evidence can collapse them.
PASTE_RE = re.compile(r'\[pasted: (\d+) chars\].*?\[/pasted\]', re.S)
EVIDENCE_NOTE = ('Built by the commit script from the source records, not written by the model. One line per '
                 'ref cited above: ref | speaker (owner, agent, tool, call, event, other) | source | local time | '
                 f'verbatim excerpt (redacted; up to {EXCERPT_LIMIT} characters, with [...] where staging cut a '
                 'long message; pastes shown as [pasted: N chars]).')

TOKEN_RE = re.compile(
    r'\b(?:sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}'
    r'|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|\d{8,10}:AA[A-Za-z0-9_-]{30,}'
    r'|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|ntn_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,})')
PRIVATE_KEY_RE = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)', re.S)
URL_CRED_RE = re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@')
BEARER_RE = re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+')
ASSIGN_RE = re.compile(r'''(?ix)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|password|passwd
                       |secret|client[_-]?secret)\s*["']?\s*[:=]\s*["']?)[^\s,"'}]+''')


class DraftRejected(ValueError):
    """The draft can be fixed and committed again in the same run."""


class SourceChanged(ValueError):
    """The day changed after staging; it stays pending and is staged again next run."""


def redact(text):
    text = PRIVATE_KEY_RE.sub('[REDACTED PRIVATE KEY]', text)
    text = TOKEN_RE.sub('[REDACTED]', text)
    text = URL_CRED_RE.sub(r'\1[REDACTED]@', text)
    text = BEARER_RE.sub(r'\1[REDACTED]', text)
    return ASSIGN_RE.sub(r'\1[REDACTED]', text)


def clip_middle(text, limit):
    """Keep the head and tail of long text, marking the cut. Returns (text, truncated)."""
    if len(text) <= limit:
        return text, False
    return text[:limit * 3 // 4] + TRUNCATION_MARK + text[-(limit // 4):], True


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8', newline='\n') as f:
            if os.name != 'nt':
                os.chmod(tmp, 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def file_lock(path):
    """Exclusive lock on a sidecar file, on Linux and Windows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a+') as f:
        if os.name == 'nt':
            import msvcrt
            import time
            for _ in range(120):  # LK_NBLCK fails at once; poll for up to a minute
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise OSError(f'Could not lock {path}')
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
            yield


def local_today(timezone):
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def day_range(start, today):
    cursor, end = date.fromisoformat(start), date.fromisoformat(today)
    while cursor < end:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def day_bounds(day, timezone):
    from datetime import time as dtime
    d, tz = date.fromisoformat(day), ZoneInfo(timezone)
    return (datetime.combine(d, dtime.min, tz).timestamp(),
            datetime.combine(d + timedelta(days=1), dtime.min, tz).timestamp())


def section(text, name):
    match = re.search(rf'^## {re.escape(name)}[ \t]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    return match.group(1).strip() if match else ''


def strip_frontmatter(text):
    if text.startswith('---\n'):
        end = text.find('\n---\n', 4)
        if end != -1:
            return text[:end + 5], text[end + 5:].lstrip('\n')
    return '', text


def strip_evidence(body):
    return re.split(r'^## Evidence[ \t]*$', body, maxsplit=1, flags=re.M)[0].rstrip() + '\n'


def excerpt(message):
    limit = EXCERPT_LIMIT
    text = PASTE_RE.sub(lambda m: f'[pasted: {m.group(1)} chars]', message.get('content') or '')
    text = text.replace(TRUNCATION_MARK, ' [...] ')
    if message.get('role') == 'tool' and message.get('call'):
        text = f"[{message['call'][:120]}] => {text}"
    text = ' '.join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + '\u2026'
    return text.replace('[[', '[\\[')  # keep excerpts from becoming Obsidian links


def build_evidence(body, messages, ref_re):
    by_ref = {m['ref']: m for m in messages}
    cited = list(dict.fromkeys(ref_re.findall(body)))
    lines = []
    for ref in cited:
        m = by_ref.get(ref)
        if m:
            who = m['role'] if m.get('role') in ('call', 'event') else m.get('speaker', '?')
            lines.append(f"- `{ref}` | {who} | {m.get('source', '?')} | {m.get('time', '?')} | {excerpt(m)}")
    if not lines:
        return '', 0
    return '## Evidence\n\n' + EVIDENCE_NOTE + '\n\n' + '\n'.join(lines) + '\n', len(lines)


class Journal:
    """One agent's journal: a working folder (state, staging, meta, revisions, published notes)
    plus an optional vault folder that receives a copy of every published note."""

    def __init__(self, source, root, vault_dir=None, reopen_days=REOPEN_DAYS, start_day=None):
        self.source = source
        self.root = Path(root)
        self.vault_dir = Path(vault_dir) if vault_dir else None
        self.reopen_days = reopen_days
        self.start_day = start_day
        self.published = []  # days published by this process, so callers know whether to sync

    # ---- state -------------------------------------------------------------------------------
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return file_lock(self.root / '.journal.lock')

    def load_state(self, today):
        path = self.root / '.state.json'
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        start = self.start_day or (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        return {'start_day': start, 'days': {}}

    def save_state(self, state):
        atomic_write(self.root / '.state.json', json.dumps(state, indent=2))

    def read_meta(self, day):
        try:
            return json.loads((self.root / '.meta' / f'{day}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def is_written(self, state, day):
        """A day is done when its note is on disk, or when it had no conversations (state['empty'], no note)."""
        return day in state['days'] and (day in state.get('empty', []) or (self.root / f'{day}.md').is_file())

    @staticmethod
    def is_empty_note(path):
        """A note from before empty days got no file: written by the script, zero messages."""
        try:
            head = strip_frontmatter(Path(path).read_text(encoding='utf-8'))[0]
        except OSError:
            return False
        return bool(re.search(r'^written_by: script$', head, re.M) and re.search(r'^messages: 0$', head, re.M))

    def mark_empty(self, state, day, fingerprint):
        state['days'][day] = fingerprint
        state['empty'] = sorted({*state.get('empty', []), day})

    def retire_empty_notes(self, state, log=None):
        """Empty days used to get a note saying so. Drop those local notes and remember the days as empty;
        copies already in the vault are not touched (the vault's own history decides those)."""
        for day in sorted(state['days']):
            path = self.root / f'{day}.md'
            if day not in state.get('empty', []) and path.is_file() and self.is_empty_note(path):
                path.unlink()
                (self.root / '.meta' / f'{day}.json').unlink(missing_ok=True)
                self.mark_empty(state, day, state['days'][day])
                if log:
                    log(f'Retired the empty-day note for {day}')

    def today(self, today=None):
        return today or local_today(self.source.timezone)

    # ---- prepare ------------------------------------------------------------------------------
    def prepare(self, day=None, today=None, log=None):
        """Record empty days in the state (they get no note); stage and return the oldest day that needs the model."""
        today = self.today(today)
        with self.lock():
            state = self.load_state(today)
            self.retire_empty_notes(state, log)
            forced = day is not None
            if forced:
                if date.fromisoformat(day) >= date.fromisoformat(today):
                    raise ValueError('Only completed local days can be journaled')
                if day < state['start_day']:
                    state['start_day'] = day
                days = [day]
            else:
                days = list(day_range(state['start_day'], today))
            self.clean_staging(state)
            # Written days older than the reopen window are final and never read again.
            days = [d for d in days if forced or not self.is_written(state, d)
                    or (date.fromisoformat(today) - date.fromisoformat(d)).days <= self.reopen_days]
            self.source.begin(days)
            for candidate in days:  # oldest first, so a backlog or a reopened day never starves
                written = self.is_written(state, candidate)
                collected = self.source.collect(candidate)
                if written and state['days'][candidate] == collected['fingerprint']:
                    continue
                if written and not forced and collected['message_count'] <= self.read_meta(candidate).get('message_count', 0):
                    continue  # edits, rewinds or pruning, not new conversation: keep the note we have
                if collected['message_count'] == 0:
                    if (self.root / f'{candidate}.md').is_file():
                        continue  # a forced redo of a day whose conversations are gone keeps the note it has
                    self.mark_empty(state, candidate, collected['fingerprint'])
                    if log:
                        log(f'No conversations on {candidate}; no note written')
                    continue
                task = self.stage(collected)
                self.save_state(state)
                return task
            self.save_state(state)
            return None

    def clean_staging(self, state):
        root = self.root / '.staging'
        if root.is_dir():
            for path in root.iterdir():
                if path.is_dir() and DAY_RE.match(path.name) and self.is_written(state, path.name):
                    shutil.rmtree(path)

    def stage(self, collected):
        day = collected['day']
        staging = self.root / '.staging' / day
        if staging.exists():
            shutil.rmtree(staging)  # never leave parts from an earlier pass next to the new ones
        staging.mkdir(parents=True, mode=0o700)
        messages = collected['messages']
        narrative = [m for m in messages if m['role'] != 'tool']
        evidence = [m for m in messages if m['role'] == 'tool']
        parts, blocks, size = [], [], 0

        def flush():
            path = staging / f'narrative-{len(parts) + 1:03d}.md'
            atomic_write(path, ''.join(blocks))
            parts.append(str(path))

        for block in render_blocks(narrative):
            if blocks and size + len(block) > PART_CHARS:
                flush()
                blocks, size = [], 0
            blocks.append(block)
            size += len(block)
        if blocks:
            flush()
        manifest = {k: v for k, v in collected.items() if k != 'messages'}
        manifest.update({'parts': parts, 'narrative_messages': len(narrative),
                         'tool_result_records': len(evidence), 'refs': [m['ref'] for m in messages],
                         'draft_path': str(staging / 'draft.md')})
        if evidence:
            evidence_path = staging / 'tool-evidence.jsonl'
            atomic_write(evidence_path, ''.join(json.dumps(m, ensure_ascii=False) + '\n' for m in evidence))
            manifest['tool_evidence_path'] = str(evidence_path)
        atomic_write(staging / 'manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False))
        return {k: v for k, v in manifest.items() if k != 'refs'}

    # ---- commit -------------------------------------------------------------------------------
    def commit(self, day, today=None):
        today = self.today(today)
        if date.fromisoformat(day) >= date.fromisoformat(today):
            raise ValueError('Only completed local days can be committed')
        with self.lock():
            manifest_path = self.root / '.staging' / day / 'manifest.json'
            if not manifest_path.is_file():
                raise ValueError(f'Nothing staged for {day}; run prepare first')
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            draft = Path(manifest['draft_path'])
            if not draft.is_file():
                raise DraftRejected(f'No draft at {draft}')
            text = self.validate_draft(draft.read_text(encoding='utf-8'), day, set(manifest['refs']),
                                       manifest['message_count'])
            self.source.begin([day])
            collected = self.source.collect(day)
            if collected['fingerprint'] != manifest['fingerprint']:
                raise SourceChanged(f'{day} changed after staging; it stays pending and is staged again next run')
            evidence, count = build_evidence(text, collected['messages'], self.source.ref_re)
            state = self.load_state(today)
            final = self.publish(manifest, text + ('\n\n' + evidence if evidence else ''), 'agent', state,
                                 evidence_refs=count)
            result = {'path': str(final), 'day': day, 'source_messages': manifest['message_count'],
                      'evidence_refs': count, 'fingerprint': manifest['fingerprint']}
            if self.vault_dir:
                result['vault_path'] = str(self.vault_path(day))
            return result

    def validate_draft(self, text, day, valid_refs, message_count):
        text = text.strip()
        lines = text.splitlines()
        problems = []
        if text.startswith('---'):
            problems.append('Remove the frontmatter; commit adds it.')
        if not lines or lines[0].strip() != f'# {day}':
            problems.append(f'The first line must be "# {day}".')
        headings = [line[3:].strip() for line in lines if line.startswith('## ')]
        if 'Evidence' in headings:
            problems.append('Remove the Evidence section; commit builds it from the source.')
        missing = [s for s in SECTIONS if s not in headings]
        in_order = iter(headings)
        if missing:
            problems.append('Missing sections: ' + ', '.join(missing) + '.')
        elif not all(s in in_order for s in SECTIONS):
            problems.append('Sections must appear in this order: ' + ', '.join(SECTIONS) + '.')
        summary = section(text, 'Summary')
        if len(summary) > SUMMARY_LIMIT:
            problems.append(f'Summary is {len(summary)} characters; keep it to two to four sentences '
                            f'(under {SUMMARY_LIMIT}).')
        if len(text) > DRAFT_LIMIT:
            problems.append(f'Draft is {len(text)} characters; the limit is {DRAFT_LIMIT}.')
        if '[[' in text:
            problems.append('Remove [[wikilinks]]; write plain names.')
        refs = set(self.source.ref_re.findall(text))
        invalid = sorted(refs - valid_refs)
        if invalid:
            problems.append("References not in this day's manifest: " + ', '.join(invalid[:10]) + '.')
        if message_count and not refs:
            problems.append(self.source.ref_hint)
        flagged = [f'line {n}: {redact(line).strip()[:160]}' for n, line in enumerate(lines, 1) if redact(line) != line]
        if flagged:
            problems.append('Possible credential values (reword or drop them; masked here): ' + '; '.join(flagged[:5]))
        if problems:
            raise DraftRejected('Draft rejected. ' + ' '.join(problems))
        return text

    # ---- publish ------------------------------------------------------------------------------
    def publish(self, manifest, body, written_by, state, evidence_refs=0):
        day = manifest['day']
        final = self.root / f'{day}.md'
        created = None
        if final.exists():
            old = final.read_text(encoding='utf-8')
            atomic_write(self.root / '.revisions' / day / f'{uuid.uuid4().hex}.md', old)
            created = re.search(r'^created: (\S+)$', strip_frontmatter(old)[0], re.M)
            created = created.group(1) if created else None
        now = datetime.now(dt_timezone.utc)
        generated = now.isoformat(timespec='seconds')
        local = now.astimezone(ZoneInfo(self.source.timezone)).date().isoformat()
        text = self.frontmatter(manifest, written_by, generated, created or local, local, evidence_refs)
        text += body.strip() + '\n'
        self.copy_to_vault(day, text)
        atomic_write(final, text)
        meta = {k: v for k, v in manifest.items() if k not in ('messages', 'refs')}
        meta.update({'written_by': written_by, 'generated': generated, 'evidence_refs': evidence_refs})
        atomic_write(self.root / '.meta' / f'{day}.json', json.dumps(meta, indent=2, ensure_ascii=False))
        state['days'][day] = manifest['fingerprint']
        if day in state.get('empty', []):  # conversations arrived after the day was recorded as empty
            state['empty'].remove(day)
        self.save_state(state)
        staging = self.root / '.staging' / day
        if staging.exists():
            shutil.rmtree(staging)
        self.published.append(day)
        return final

    def vault_path(self, day):
        """Published notes sit in one folder per month (journal/hermes/2026-11/2026-11-18.md), so no folder
        ever holds more than a month of notes."""
        return self.vault_dir / day[:7] / f'{day}.md'

    def copy_to_vault(self, day, text):
        if self.vault_dir:
            path = self.vault_path(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Publish a complete file without replacing any existing vault note.
            fd, temp = tempfile.mkstemp(prefix='.wake-', dir=path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.link(temp, path)
                except FileExistsError:
                    if path.is_symlink() or not path.is_file() or path.read_bytes() != text.encode('utf-8'):
                        raise FileExistsError(f'Vault note already exists; not overwritten: {path}')
            finally:
                os.unlink(temp)

    def frontmatter(self, m, written_by, generated, created, updated, evidence_refs):
        day = m['day']
        fields = [('type', 'journal'), ('date', day), ('weekday', date.fromisoformat(day).strftime('%A')),
                  ('agent', self.source.agent), *self.source.frontmatter_extra(m),
                  ('timezone', m['timezone']), ('messages', m['message_count']),
                  ('narrative_messages', m.get('narrative_messages', 0)),
                  ('tool_records', m.get('tool_result_records', 0)), ('sessions', m.get('session_count', 0)),
                  ('truncated_excerpts', m.get('truncated_excerpts', 0)), ('evidence_refs', evidence_refs),
                  ('written_by', written_by), ('source_version', m.get('source_version')),
                  ('fingerprint', m['fingerprint']), ('created', created), ('updated', updated),
                  ('generated', generated)]
        return ('---\n' + ''.join(f'{k}: {v}\n' for k, v in fields) +
                f'tags:\n  - journal\n  - journal/{self.source.agent}\n---\n\n')

    # ---- evidence backfill ----------------------------------------------------------------------
    def rebuild_evidence(self, days=None, today=None, log=None):
        """Add or refresh the Evidence section of already written notes, keeping their text and
        frontmatter, and copy them to the vault. Used once to backfill notes written before Evidence."""
        today = self.today(today)
        done = []
        with self.lock():
            state = self.load_state(today)
            targets = days or sorted(d for d in state['days'] if self.is_written(state, d))
            self.source.begin(targets)
            for day in targets:
                path = self.root / f'{day}.md'
                if not path.is_file() or self.is_empty_note(path):
                    continue  # empty days have no note, and an old empty one must not be copied to the vault
                old = path.read_text(encoding='utf-8')
                head, body = strip_frontmatter(old)
                body = strip_evidence(body)
                evidence, count = build_evidence(body, self.source.collect(day)['messages'], self.source.ref_re)
                if count:
                    head = re.sub(r'^evidence_refs: .*\n', '', head, flags=re.M)
                    head = head.replace('\nwritten_by:', f'\nevidence_refs: {count}\nwritten_by:', 1)
                new = head + ('\n' if head else '') + body.rstrip() + ('\n\n' + evidence if evidence else '\n')
                if head and (new != old or not re.search(r'^created: ', head, re.M)):
                    new = self.stamp_dates(new, today)
                self.copy_to_vault(day, new)
                if new != old:
                    atomic_write(self.root / '.revisions' / day / f'{uuid.uuid4().hex}.md', old)
                    atomic_write(path, new)
                meta_path = self.root / '.meta' / f'{day}.json'
                if meta_path.is_file():  # status reads the count from here
                    meta = json.loads(meta_path.read_text(encoding='utf-8'))
                    if meta.get('evidence_refs') != count:
                        meta['evidence_refs'] = count
                        atomic_write(meta_path, json.dumps(meta, indent=2, ensure_ascii=False))
                self.published.append(day)
                done.append((day, count))
                if log:
                    log(f'{day}: {count} evidence refs')
        return done

    def stamp_dates(self, text, today):
        """Set `updated` to today and add `created` (from `generated`) to notes written before they existed."""
        head, body = strip_frontmatter(text)
        head = re.sub(r'^updated: .*\n', '', head, flags=re.M)
        if not re.search(r'^created: ', head, re.M):
            generated = re.search(r'^generated: (\S+)$', head, re.M)
            created = today
            if generated:
                try:
                    created = (datetime.fromisoformat(generated.group(1))
                               .astimezone(ZoneInfo(self.source.timezone)).date().isoformat())
                except ValueError:
                    pass
            head = head.replace('\n---\n', f'\ncreated: {created}\n---\n', 1) if '\ngenerated:' not in head else \
                head.replace('\ngenerated:', f'\ncreated: {created}\ngenerated:', 1)
        anchor = '\ngenerated:' if '\ngenerated:' in head else '\n---\n'
        head = head.replace(anchor, f'\nupdated: {today}{anchor}', 1)
        return head + '\n' + body

    # ---- status -------------------------------------------------------------------------------
    def status(self, today=None, recent=14):
        today = self.today(today)
        state = self.load_state(today)
        days = list(day_range(state['start_day'], today))
        out = [f'Journal {self.root} ({self.source.agent}); today is {today} ({self.source.timezone}); '
               f'written days are final after {self.reopen_days} days.']
        if self.vault_dir:
            out.append(f'Published copies go to {self.vault_dir}.')
        older = days[:-recent]
        if older:
            done = sum(self.is_written(state, d) for d in older)
            out.append(f'{older[0]} to {older[-1]}: {done} written, {len(older) - done} pending.')
        for d in days[-recent:]:
            final = self.root / f'{d}.md'
            if d in state.get('empty', []):
                out.append(f'{d}  no conversations, no note')
            elif self.is_written(state, d):
                meta = self.read_meta(d)
                modified = datetime.fromtimestamp(final.stat().st_mtime, ZoneInfo(self.source.timezone))
                out.append(f"{d}  written by {meta.get('written_by', 'agent')}, {meta.get('message_count', '?')} "
                           f"messages, {meta.get('evidence_refs', 0)} evidence refs, "
                           f"file modified {modified:%Y-%m-%d %H:%M}")
            elif (self.root / '.staging' / d / 'manifest.json').is_file():
                out.append(f"{d}  staged, waiting for the agent's commit")
            else:
                out.append(f'{d}  pending')
        return '\n'.join(out)


def wrap_content(text):
    return '\n'.join('\n'.join(textwrap.wrap(line, width=240, replace_whitespace=False)) if line else ''
                     for line in text.splitlines())


LABELS = {'owner': 'owner', 'agent': 'agent', 'tool': 'tool', 'other': 'not owner'}
MAX_RUN_LINES = 8


def render_blocks(messages):
    """Narrative blocks: one per message, except runs of tool calls in one session, which share a block
    (at most MAX_RUN_LINES lines; the rest are counted)."""
    blocks, i = [], 0
    while i < len(messages):
        m = messages[i]
        if m['role'] == 'call':
            run = [m]
            while (i + len(run) < len(messages) and messages[i + len(run)]['role'] == 'call'
                   and messages[i + len(run)].get('session') == m.get('session')):
                run.append(messages[i + len(run)])
            lines = []
            for c in run:
                for n, line in enumerate(c['content'].splitlines()):
                    lines.append(f"- {c['ref'] if n == 0 else ' ' * len(c['ref'])} {line}")
            if len(lines) > MAX_RUN_LINES:
                keep = MAX_RUN_LINES - 2
                lines = lines[:keep] + [f'- (+{len(lines) - keep - 1} more calls)'] + lines[-1:]
            blocks.append(f"### {m['ref']} | calls | {m['source']} | {m['time']}\n" + '\n'.join(lines) + '\n\n')
            i += len(run)
            continue
        label = LABELS.get(m.get('speaker'), m.get('speaker', '?'))
        if m['role'] == 'event':
            label = 'event'
        elif m['role'] == 'user' and m.get('speaker') != 'owner':
            label = 'user (not owner)'
        flag = ' | TRUNCATED' if m.get('truncated') else ''
        blocks.append(f"### {m['ref']} | {label} | {m['source']} | {m['time']}{flag}\n{wrap_content(m['content'])}\n\n")
        i += 1
    return blocks
