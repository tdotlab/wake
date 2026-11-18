"""Hermes source: one local day of default-profile conversation from state.db, opened read-only."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import sqlite3

from ..core import CALL_LINE_LIMIT, clip_middle, day_bounds, redact

SOURCE_VERSION = 5  # 5: the mobile app ('gui') is the owner; injected '[System: ...' user turns are not
NARRATIVE_LIMIT = 12000
TOOL_LIMIT = 700
# Sessions where the "user" is the owner. Others (subagent, a2a, cron, oneshot, bot_room) carry agent-written
# prompts or several speakers. 'gui' is the owner's mobile app.
OWNER_SOURCES = {'telegram', 'cli', 'webui', 'gui', 'discord', 'signal', 'whatsapp', 'slack', 'matrix'}
# User-role turns the gateway injects, e.g. "[System: The active model for this chat has changed...]".
SYSTEM_PREFIX = '[System: '


class HermesSource:
    agent = 'hermes'
    ref_re = re.compile(r's:[A-Za-z0-9_.-]+/m:[0-9]+')
    ref_hint = 'Cite sources as s:<session>/m:<id>.'

    def __init__(self, db_path, timezone='UTC'):
        self.db_path = Path(db_path)
        self.timezone = timezone

    def begin(self, days):
        pass

    def frontmatter_extra(self, manifest):
        return [('profile', 'default')]

    def collect(self, day):
        start, end = day_bounds(day, self.timezone)
        tz = ZoneInfo(self.timezone)
        db = sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('''SELECT m.id, m.session_id, m.role, m.content, m.tool_name, m.tool_call_id,
                       m.tool_calls, m.timestamp, m.message_uid, m._compressed_summary, s.source
                FROM messages m JOIN sessions s ON s.id = m.session_id
                WHERE m.timestamp >= ? AND m.timestamp < ? AND s.source != 'cron'
                  AND m.role IN ('user', 'assistant', 'tool')
                ORDER BY m.timestamp, m.id''', (start, end)).fetchall()
        finally:
            db.close()
        calls = {}
        for row in rows:
            if row['role'] == 'assistant' and row['tool_calls']:
                calls.update(describe_calls(row['tool_calls']))
        messages, seen, excluded = [], set(), 0
        for row in rows:
            content = str(row['content'] or '').strip()
            uid = row['message_uid'] or (row['session_id'], row['id'])
            # Compaction copies kept messages forward under the same uid; tool-call-only assistant turns
            # have no text (their calls are attached to the matching tool results instead).
            if (uid in seen or row['_compressed_summary'] or not content or
                    content.startswith(('[CONTEXT COMPACTION', '[IMPORTANT: Background process'))):
                excluded += 1
                continue
            seen.add(uid)
            cleaned, truncated = clip_middle(redact(content), TOOL_LIMIT if row['role'] == 'tool' else NARRATIVE_LIMIT)
            role, source = row['role'], row['source']
            speaker = {'assistant': 'agent', 'tool': 'tool'}.get(role) or (
                'owner' if source in OWNER_SOURCES and not content.startswith(SYSTEM_PREFIX) else 'other')
            message = {'ref': f"s:{row['session_id']}/m:{row['id']}", 'session': row['session_id'],
                       'message_id': row['id'], 'role': role, 'speaker': speaker, 'source': source,
                       'tool': row['tool_name'], 'timestamp': row['timestamp'],
                       'time': datetime.fromtimestamp(row['timestamp'], tz).strftime('%H:%M'),
                       'truncated': truncated, 'content': cleaned}
            if role == 'tool':
                message['call'] = calls.get(row['tool_call_id'])
            messages.append(message)
        digest = hashlib.sha256(json.dumps({'version': SOURCE_VERSION, 'messages': messages}, sort_keys=True,
                                           ensure_ascii=False).encode()).hexdigest()
        sessions = sorted({m['session'] for m in messages})
        return {'source_version': SOURCE_VERSION, 'agent': self.agent, 'day': day, 'timezone': self.timezone,
                'window_start': start, 'window_end': end,
                'scope': 'default profile, non-cron sessions; message timestamps, not session start dates',
                'fingerprint': digest, 'message_count': len(messages), 'excluded_rows': excluded,
                'truncated_excerpts': sum(m['truncated'] for m in messages), 'sessions': sessions,
                'session_count': len(sessions), 'messages': messages}


def describe_calls(raw):
    """Map tool_call ids to a short 'name: args' line, so a tool result says what it answered."""
    try:
        calls = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    described = {}
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict):
            continue
        fn = call.get('function') or {}
        args = fn.get('arguments', '')
        try:
            args = json.loads(args) if isinstance(args, str) and args else args
        except ValueError:
            pass
        if isinstance(args, dict):
            args = ' '.join(f'{k}={v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}'
                            for k, v in args.items())
        line = ' '.join(redact(f"{fn.get('name') or call.get('name') or 'tool'}: {args}").split())
        line = line if len(line) <= CALL_LINE_LIMIT else line[:CALL_LINE_LIMIT] + '...'
        for key in (call.get('id'), call.get('call_id')):
            if key:
                described[key] = line
    return described
