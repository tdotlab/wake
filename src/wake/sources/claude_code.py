"""Claude Code source: one local day of the user's Claude Code conversations from ~/.claude/projects.

Kept: the user's typed prompts (records whose origin kind is "human") and slash commands; Claude's prose; one
line per tool call (name plus command or path, never the output); background notices and messages from
other sessions as events. Dropped: thinking, tool output, compaction summaries, meta records, sidechains
and subagents, headless (sdk) runs, and sessions where the user never typed anything (scheduled tasks).
Raw transcripts never leave the machine; only the journal note does.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import re

from ..core import clip_middle, day_bounds, redact

SOURCE_VERSION = 1
T_LIMIT = 12000
PASTE_LIMIT = 1000
EVENT_LIMIT = 400
# Claude's prose and call lines shrink in steps until the day's narrative fits the budget.
PROSE_CAPS = (4000, 2000, 1200, 700)
CALL_CAPS = (300, 160, 100)
NARRATIVE_BUDGET = 450000
SKIP_TOOLS = {'update_plan', 'ToolSearch', 'TodoWrite', 'read_me', 'mark_chapter'}
PASTE_TAG = re.compile(r'<pasted_content[^>]*>(.*?)</pasted_content[^>]*>', re.S)
REMINDER_TAG = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
TAG = re.compile(r'</?[a-zA-Z][\w-]*[^>]*>')


def parse_ts(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def project_label(cwd):
    if not cwd:
        return 'unknown'
    if 'scratch-workspaces' in cwd.replace('\\', '/'):
        return 'scratch'
    return Path(cwd.replace('\\', '/')).name or cwd


def text_of(content, images=False):
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict):
            if block.get('type') == 'text':
                parts.append(block.get('text', ''))
            elif images and block.get('type') == 'image':
                parts.append('[image]')
    return '\n'.join(parts)


def clean_human(content):
    text = REMINDER_TAG.sub('', text_of(content, images=True))

    def paste(m):
        body = m.group(1).strip()
        clipped, _ = clip_middle(body, PASTE_LIMIT)
        return f'[pasted: {len(body)} chars]\n{clipped}\n[/pasted]'
    return PASTE_TAG.sub(paste, text).strip()


def describe_tool(name, args):
    """One line per tool call: the tool and what it touched, never its output."""
    short = name.split('__')[-1] if name.startswith('mcp__') else name
    if short in SKIP_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    if name in ('Bash', 'PowerShell'):
        detail = args.get('command', '')
    elif name in ('Read', 'Write', 'Edit', 'NotebookEdit'):
        detail = args.get('file_path') or args.get('notebook_path', '')
    elif name == 'Grep':
        detail = f"{args.get('pattern', '')} in {args.get('path') or args.get('glob') or '.'}"
    elif name == 'Glob':
        detail = f"{args.get('pattern', '')} in {args.get('path', '.')}"
    elif name == 'WebFetch':
        detail = args.get('url', '')
    elif name == 'WebSearch':
        detail = args.get('query', '')
    elif name in ('Agent', 'Task'):
        detail = args.get('description', '')
    elif name == 'Skill':
        detail = f"{args.get('skill', '')} {args.get('args', '')}".strip()
    elif short == 'wrap_up':
        detail = args.get('recap', '')
    else:
        detail = json.dumps(args, ensure_ascii=False, sort_keys=True)
    prefix = f'mcp:{short}' if name.startswith('mcp__') else name
    return ' '.join(redact(f'{prefix}: {detail}').split())


class ClaudeCodeSource:
    agent = 'claude-code'
    ref_re = re.compile(r'c:[0-9a-f]{8}/u:[0-9a-f]{8}')
    ref_hint = 'Cite sources as c:<session8>/u:<uuid8>, copied from the block headers or call lines.'

    def __init__(self, projects_dir, timezone='UTC', exclude_dirs=()):
        self.projects_dir = Path(projects_dir)
        self.timezone = timezone
        self.exclude = [str(Path(d)).replace('\\', '/').lower().rstrip('/') for d in exclude_dirs]
        self.by_day = {}

    def frontmatter_extra(self, manifest):
        projects = sorted({s['project'] for s in manifest.get('sessions', [])})
        return [('projects', '[' + ', '.join(json.dumps(p) for p in projects) + ']')]

    def excluded(self, cwd):
        cwd = (cwd or '').replace('\\', '/').lower().rstrip('/')
        return any(cwd == d or cwd.startswith(d + '/') for d in self.exclude)

    def begin(self, days):
        """Parse every transcript that could hold records for these days, once per process."""
        self.by_day = {d: [] for d in days}
        if not days:
            return
        since = day_bounds(min(days), self.timezone)[0]
        tz = ZoneInfo(self.timezone)
        sessions = []
        for path in sorted(self.projects_dir.glob('*/*.jsonl')):  # subagents live one level deeper
            if path.stat().st_mtime < since:
                continue
            session = self.read_session(path)
            if session:
                sessions.append(session)
        sessions.sort(key=lambda s: (s['first'], s['born'], s['file']))  # a fork ties on time; the older file wins
        owned = set()  # a uuid copied into a fork belongs to the session that had it first
        for s in sessions:
            for r in s['records']:
                if r['uuid'] in owned:
                    continue
                owned.add(r['uuid'])
                day = datetime.fromtimestamp(r['ts'], tz).date().isoformat()
                if day in self.by_day:
                    self.by_day[day].append((s, r))

    def read_session(self, path):
        records, title, human, cwd = {}, None, False, None
        with path.open(encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                kind = r.get('type')
                if kind == 'custom-title':
                    title = r.get('customTitle') or title
                if kind not in ('user', 'assistant') or not r.get('uuid') or not r.get('timestamp'):
                    continue
                if r.get('isSidechain') or str(r.get('entrypoint', '')).startswith('sdk'):
                    continue
                if (r.get('origin') or {}).get('kind') == 'human':
                    human = True
                cwd = cwd or r.get('cwd')
                message = r.get('message') or {}
                records[r['uuid']] = {  # a later copy of the same uuid in one file is the complete one
                    'uuid': r['uuid'], 'session': r.get('sessionId') or path.stem, 'type': kind,
                    'ts': parse_ts(r['timestamp']), 'content': message.get('content'),
                    'origin': (r.get('origin') or {}).get('kind'), 'meta': bool(r.get('isMeta')),
                    'compact': bool(r.get('isCompactSummary') or r.get('isVisibleInTranscriptOnly')),
                    'api_error': bool(r.get('isApiErrorMessage'))}
        if not human or not records or self.excluded(cwd):
            return None
        ordered = sorted(records.values(), key=lambda r: r['ts'])
        stat = path.stat()
        return {'file': path.name, 'first': ordered[0]['ts'], 'born': getattr(stat, 'st_birthtime', stat.st_ctime),
                'title': title, 'project': project_label(cwd), 'records': ordered}

    def collect(self, day):
        if day not in self.by_day:
            self.begin([day])
        tz = ZoneInfo(self.timezone)
        start, end = day_bounds(day, self.timezone)
        raw, excluded = [], 0
        for s, r in self.by_day[day]:
            produced = self.convert(r)
            if not produced:
                excluded += 1
            for m in produced:
                m.update({'ref': f"c:{r['session'][:8]}/u:{r['uuid'][:8]}", 'session': r['session'],
                          'source': s['project'], 'timestamp': r['ts'],
                          'time': datetime.fromtimestamp(r['ts'], tz).strftime('%H:%M')})
                raw.append((s, m))
        prose_cap, call_cap = self.fit_caps([m for _, m in raw])
        firsts = {}
        for s, m in raw:
            firsts.setdefault(m['session'], m['timestamp'])
        raw.sort(key=lambda sm: (firsts[sm[1]['session']], sm[1]['session'], sm[1]['timestamp']))
        messages, refs = [], set()
        for s, m in raw:
            if m['ref'] in refs:  # 8-character prefixes collide only in theory; keep refs unique
                continue
            refs.add(m['ref'])
            if m['role'] == 'assistant':
                m['content'], m['truncated'] = clip_middle(m['content'], prose_cap)
            elif m['role'] == 'call':
                m['content'] = '\n'.join(line if len(line) <= call_cap else line[:call_cap] + '...'
                                         for line in m['content'].splitlines())
            messages.append(m)
        info = {}
        for s, m in raw:
            e = info.setdefault(m['session'], {'ref': 'c:' + m['session'][:8], 'project': s['project'],
                                               'title': s['title'], 'first': m['time'], 'owner_messages': 0})
            e['last'] = m['time']
            e['owner_messages'] += m['speaker'] == 'owner'
        sessions = list(info.values())
        digest = hashlib.sha256(json.dumps({'version': SOURCE_VERSION, 'messages': messages}, sort_keys=True,
                                           ensure_ascii=False).encode()).hexdigest()
        return {'source_version': SOURCE_VERSION, 'agent': self.agent, 'day': day, 'timezone': self.timezone,
                'window_start': start, 'window_end': end,
                'scope': ("the user's Claude Code sessions (interactive only); record timestamps, "
                          'not session start dates; thinking and tool output are not included'),
                'fingerprint': digest, 'message_count': len(messages), 'excluded_rows': excluded,
                'truncated_excerpts': sum(bool(m.get('truncated')) for m in messages),
                'prose_cap': prose_cap, 'call_line_cap': call_cap,
                'sessions': sessions, 'session_count': len(sessions), 'messages': messages}

    @staticmethod
    def fit_caps(messages):
        fixed = sum(len(m['content']) + 60 for m in messages if m['role'] not in ('assistant', 'call'))
        for prose_cap in PROSE_CAPS:
            for call_cap in CALL_CAPS:
                size = fixed + sum(min(len(m['content']), prose_cap) + 60 for m in messages if m['role'] == 'assistant')
                size += sum(sum(min(len(line), call_cap) + 14 for line in m['content'].splitlines())
                            for m in messages if m['role'] == 'call')
                if size <= NARRATIVE_BUDGET:
                    return prose_cap, call_cap
        return PROSE_CAPS[-1], CALL_CAPS[-1]

    def convert(self, r):
        """Turn one transcript record into zero or more journal messages (without ref/time/source)."""
        content = r['content']
        if r['type'] == 'assistant':
            if r['api_error']:
                return []
            blocks = content if isinstance(content, list) else [{'type': 'text', 'text': str(content or '')}]
            prose = '\n'.join(b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text').strip()
            calls = [describe_tool(b.get('name', ''), b.get('input')) for b in blocks
                     if isinstance(b, dict) and b.get('type') == 'tool_use']
            calls = [c for c in calls if c]
            if prose:
                text = redact(prose) + ''.join(f'\n[call] {c}' for c in calls)
                return [{'role': 'assistant', 'speaker': 'agent', 'content': text, 'truncated': False}]
            if calls:
                return [{'role': 'call', 'speaker': 'agent', 'content': '\n'.join(calls), 'truncated': False}]
            return []
        if isinstance(content, list) and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content):
            return []
        raw = text_of(content).strip()
        if r['origin'] in ('task-notification', 'peer'):  # peer messages are flagged meta; keep them as events
            summary = re.search(r'<summary>(.*?)</summary>', raw, re.S)
            body = summary.group(1) if summary else TAG.sub(' ', raw)
            label = 'background task' if r['origin'] == 'task-notification' else 'message from another session'
            text, _ = clip_middle(redact(' '.join(body.split())), EVENT_LIMIT)
            return [{'role': 'event', 'speaker': 'other', 'content': f'[{label}] {text}', 'truncated': False}]
        if r['meta'] or r['compact']:
            return []
        if r['origin'] == 'human':
            text = clean_human(content)
            if not text:
                return []
            text, truncated = clip_middle(redact(text), T_LIMIT)
            return [{'role': 'user', 'speaker': 'owner', 'content': text, 'truncated': truncated}]
        if r['origin'] is None and raw.startswith(('<command-name>', '<command-message>')):
            name = re.search(r'<command-name>(.*?)</command-name>', raw, re.S)
            args = re.search(r'<command-args>(.*?)</command-args>', raw, re.S)
            command = ' '.join(x.group(1).strip() for x in (name, args) if x and x.group(1).strip())
            if command:
                return [{'role': 'user', 'speaker': 'owner', 'content': redact(command), 'truncated': False}]
            return []
        if r['origin'] is None and raw.startswith('[Request interrupted by user'):
            return [{'role': 'event', 'speaker': 'other', 'content': '[user interrupted the turn]', 'truncated': False}]
        return []
