# Wake

A nightly journal of what your AI agents did. Each night, Wake reads an agent's conversations from the day before, has a model write a short note about them, and files the note in a git-tracked vault (an Obsidian vault works well). One note per agent per day. Days with no conversations get no note.

It is built for two agents today: [Hermes](https://github.com/NousResearch/hermes-agent) and [Claude Code](https://claude.com/claude-code). Adding another is one small adapter (see [docs/DESIGN.md](docs/DESIGN.md)).

## What a note looks like

```markdown
---
type: journal
date: 2026-11-02
weekday: Monday
agent: claude-code
timezone: Europe/Berlin
messages: 212
sessions: 3
evidence_refs: 14
written_by: agent
tags:
  - journal
  - journal/claude-code
---
# 2026-11-02

## Summary
Mostly the importer. The parser now handles the new export format; tests pass.

## Decisions and corrections
- Keep the old format working until the end of the month. (c:1a2b3c4d/u:5e6f7a8b)

## Work and outcomes
- importer: rewrote the date parsing, committed, tests green. (c:1a2b3c4d/u:9c0d1e2f)

## Open threads
- Docs for the new format are still unwritten.

## Coverage and uncertainty
- Tool output is not recorded, so "tests green" is Claude's report, not a checked fact.

## Evidence
- `c:1a2b3c4d/u:5e6f7a8b` | owner | my-project | 14:02 | keep the old format working until...
```

The model writes everything above `## Evidence`. A script builds the Evidence section afterwards, from the real records, one line per ref the note cites, with verbatim excerpts. The summary is a secondary account that can be wrong; Evidence is how you check it.

## Why it is built this way

- **The model never touches your files.** It reads a staging folder and writes one draft. A script validates the draft (the required sections in order, a short summary, refs that exist in that day's record, no credential-looking values, no wikilinks, no Evidence section of its own), then publishes it. Rejected drafts go back to the model for a fix.
- **Only your words count as yours.** Every message is tagged owner, agent, tool or other (a subagent brief, a peer, a notice). The prompts tell the model not to attribute anything else to you.
- **Evidence over reflection.** Notes cite refs, and Evidence quotes the source, so a later reader (human or agent) can check a claim instead of trusting a summary of a summary.
- **Secrets are redacted before the model sees anything**, using common token and password patterns. This is a safety net, not a guarantee; see Privacy.

## Requirements

- Python 3.11 or newer. On Windows, `tzdata` is installed for you by `pip` (Windows Python ships no time zone data).
- `git`, for syncing the vault.
- For the Claude Code journal: the `claude` CLI, logged in.
- For the Hermes journal: a Hermes install with its `state.db`.

## Setup

```bash
git clone https://github.com/tdotlab/wake.git
cd wake
python -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip
```

Pick the vault (a git clone where notes will appear under `journal/<agent>/YYYY-MM/`, one folder per month) and your time zone. These are environment variables so scheduled jobs see them too:

| Variable | Meaning | Default |
|---|---|---|
| `WAKE_VAULT` | the vault folder (must be a git clone) | `~/vault` |
| `WAKE_TIMEZONE` | IANA time zone that decides where a day ends, e.g. `Europe/Berlin` | `UTC` |
| `WAKE_START_DAY` | earliest day to backfill, `YYYY-MM-DD` | yesterday |
| `HERMES_HOME` | Hermes data folder | `~/.hermes` |

Set them permanently: on Windows with `setx WAKE_TIMEZONE Europe/Berlin` (and the same for the others), on Linux in your shell profile or the cron environment.

Without a vault, journals are still written, just to a working folder (`~/.wake/claude-code-journal`, `~/.hermes/journal`).

## Usage

Claude Code, a nightly run that stages a day, writes the note and publishes it:

```bash
.venv/bin/python -m wake cc-journal run --max-days 3
```

The low-level steps, for either agent (`hermes` or `claude-code`):

```bash
.venv/bin/python -m wake journal claude-code status
.venv/bin/python -m wake journal claude-code prepare
.venv/bin/python -m wake journal claude-code commit --day 2026-11-02
```

`status` shows each day as written, staged, pending or empty. `evidence` rebuilds the Evidence section of notes already written. `wake vault-sync` pulls, commits the `journal/` folder and pushes.

## Scheduling

**Claude Code journal on Windows.** `deploy/install-windows-tasks.ps1` registers two Task Scheduler jobs for the current user (no admin): the journal at 01:00, catching up after sleep, and a vault sync every 30 minutes. Both run hidden. Logs go to `~/.wake/logs/`. Rerun the script to update the tasks, or pass `-Remove`.

**Claude Code journal on macOS or Linux.** Run `python -m wake cc-journal run` from cron or a systemd timer, daily:

```cron
0 1 * * *  WAKE_TIMEZONE=Europe/Berlin /path/to/wake/.venv/bin/python -m wake cc-journal run
```

**Hermes journal.** Hermes runs a pre-run script and, if there is work, an agent that writes the draft. `deploy/hermes/daily_journal.py` is that script and `skills/hermes-daily-journal/SKILL.md` is the skill the agent follows. `deploy/deploy-hermes.sh` copies the package, both shims, the skill and the tests to the machine that runs Hermes (set `HOST`, and `HERMES_USER` if it is not `hermes`). Then add a Hermes cron job that runs the script shortly after midnight and a second no-agent job running `vault_sync.py` every 30 minutes. See Hermes's cron documentation for the exact commands.

## Privacy

Transcripts can contain anything you ever typed to an agent. Notes and their Evidence sections hold verbatim excerpts of them.

- The model run for Claude Code is locked down: only Read, Write and Edit, confined to a staging folder, no MCP servers, no settings, hooks or CLAUDE.md, no session persistence. Raw transcripts stay on your machine; the model provider sees the staged text, like any other Claude Code session.
- Credential redaction is pattern-based and will miss things. Keep the vault private unless you have read your notes.
- Never publish a vault's `journal/` folder without reading it first.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

The tests use synthetic fixtures only. Nothing in them is real conversation text.

## License

MIT. See [LICENSE](LICENSE).
