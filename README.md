# Wake

A second brain for your AI agents. One command creates an [Obsidian](https://obsidian.md) vault of plain Markdown with rules that every agent follows: read it before guessing, write to it when you learn something that should outlive the session. Hermes, Claude Code, Codex and anything else that can read files share the same memory, and you read and edit it in Obsidian.

On top of that sits the part that makes Wake more than a folder of notes: **nightly journals**. Each night Wake reads an agent's conversations from the day before, has a model write a short note about them, and files it in the vault with an Evidence section quoting the source, so what the agents *did* is on record next to what they *know*.

Both halves are optional. Use the vault alone, the journals alone, or both.

## The starter vault

```bash
git clone https://github.com/tdotlab/wake.git
cd wake
python -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip
.venv/bin/wake init ~/vault --owner "Your Name"
```

`wake init` creates the vault, runs `git init` and makes one commit. Then:

1. Open the folder in Obsidian ("Open folder as vault"), starting at `Home.md`.
2. Tell your tools where it is: `export WAKE_VAULT=~/vault` (Windows: `setx WAKE_VAULT "C:\path\to\vault"`).
3. Give your agents the skills: `wake install-skills --claude --hermes`.
4. In your first chat, ask an agent to read the brain and interview you. It fills in `me/Profile.md`, `me/Working Preferences.md` and `me/Setup.md`.

The vault is yours from the first commit. Wake does not need to stay installed for it to work: `_system/scripts/brain.py` is a standalone, standard-library helper that lives inside the vault.

If you already have a folder you want to build on, `wake init <folder> --merge` adds only the files that are missing and never overwrites anything.

### What is in it

| Path | What |
|---|---|
| `AGENTS.md` | The rules agents follow. `CLAUDE.md` just points at it. |
| `hot.md`, `index.md`, `log.md` | What's live right now, a catalog of every note, and an append-only history. |
| `me/` | Who you are, how you like to work, your machines and tools. Starts as seed notes the first chat fills in. |
| `projects/`, `people/`, `wiki/` | Project notes, people and pets, and a wiki of entities, concepts, sources and filed answers. |
| `inbox/`, `raw/` | Drop things in `inbox/`; ingest keeps an untouched copy in `raw/` and writes a source note. |
| `daily/`, `notes/` | Your own space. Agents read it and never rewrite it. |
| `journal/` | The nightly journals, written by Wake. |
| `_system/` | Templates, the three agent skills and `brain.py`. |

It follows the pattern in Andrej Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): raw sources, a wiki the agents maintain, and a schema (`AGENTS.md`) that tells them how. You curate and ask; they do the filing, linking and bookkeeping.

### The helper

`wake brain <command>` (or `python _system/scripts/brain.py <command>` from inside the vault):

| Command | Does |
|---|---|
| `lint [--strict] [--json]` | Dead links, orphans, notes missing from the index, bad frontmatter, unresolved merge-conflict markers, a stale `hot.md`, items waiting in `inbox/`. `--strict` exits 1 on dead links, frontmatter errors and conflict markers. |
| `find <words>` | Ranked search over titles, aliases, tags and text. |
| `new <type> "<Title>"` | A note from a template: project, person, entity, concept, source, answer, daily. Never overwrites. |
| `log <op> "<Title>" --agent <name>` | Adds an entry to `log.md`. |
| `stats` | Counts by folder and type. |
| `sync -m "<message>"` | Commits everything, rebases onto the remote and pushes. On a conflict it undoes the rebase and leaves your commit alone. |

`--vault <folder>` works before or after the command. Otherwise the vault is found from `$WAKE_VAULT`, then `$BRAIN_VAULT`, then the script's own location, then the nearest folder above the working directory holding `AGENTS.md` and `hot.md`, then `~/vault`.

### Connecting agents

- **Claude Code.** `wake install-skills --claude` copies the skills to `~/.claude/skills`. Run `claude` inside the vault and it reads `CLAUDE.md`, which loads `AGENTS.md`. For work in other folders, add a line to `~/.claude/CLAUDE.md` saying where the vault is and to read its `AGENTS.md`.
- **Hermes.** `wake install-skills --hermes` copies the skills to `$HERMES_HOME/skills` (default `~/.hermes/skills`).
- **Codex and everything else.** Point the agent at `<vault>/AGENTS.md`. The skills are plain Markdown in `_system/skills/`, readable by anything.
- **Other places.** `wake install-skills --dir <folder>` copies them anywhere. Existing skills are skipped unless you pass `--force`. Skills in the vault's own `_system/skills/` win over the packaged ones, so edits you make there are what gets installed.

A vault shared by several machines is a git repo, ideally private. Each machine has a clone and agents sync through git.

## Nightly journals

One note per agent per day with conversations. Days with none get no note. Built for two agents today: [Hermes](https://github.com/NousResearch/hermes-agent) and [Claude Code](https://claude.com/claude-code). Adding another is one small adapter (see [docs/DESIGN.md](docs/DESIGN.md)).

Notes land in the vault at `journal/<agent>/YYYY-MM/YYYY-MM-DD.md`.

### What a note looks like

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
created: 2026-11-03
updated: 2026-11-03
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

(Some bookkeeping fields such as `narrative_messages` and `fingerprint` are omitted here.) The model writes everything above `## Evidence`. A script builds the Evidence section afterwards, from the real records, one line per ref the note cites, with verbatim excerpts. The summary is a secondary account that can be wrong; Evidence is how you check it.

### Why it is built this way

- **The model never touches your files.** It reads a staging folder and writes one draft. A script validates the draft (the required sections in order, a short summary, refs that exist in that day's record, no credential-looking values, no wikilinks, no Evidence section of its own), then publishes it. Rejected drafts go back to the model for a fix.
- **Only your words count as yours.** Every message is tagged owner, agent, tool or other (a subagent brief, a peer, a notice). The prompts tell the model not to attribute anything else to you.
- **Evidence over reflection.** Notes cite refs, and Evidence quotes the source, so a later reader (human or agent) can check a claim instead of trusting a summary of a summary.
- **Secrets are redacted before the model sees anything**, using common token and password patterns. This is a safety net, not a guarantee; see Privacy.
- **Agents treat journals as data.** The vault's rules tell agents that a journal quotes conversations, and a conversation can contain anything, so nothing in one is an instruction.

### Requirements

- Python 3.11 or newer. On Windows, `tzdata` is installed for you by `pip` (Windows Python ships no time zone data).
- `git`, for syncing the vault.
- For the Claude Code journal: the `claude` CLI, logged in.
- For the Hermes journal: a Hermes install with its `state.db`.

### Configuration

Environment variables, so scheduled jobs see them too:

| Variable | Meaning | Default |
|---|---|---|
| `WAKE_VAULT` | the vault folder (must be a git clone for journal sync) | `~/vault` |
| `WAKE_TIMEZONE` | IANA time zone that decides where a day ends, e.g. `Europe/Berlin` | `UTC` |
| `WAKE_START_DAY` | earliest day to backfill, `YYYY-MM-DD` | yesterday |
| `HERMES_HOME` | Hermes data folder | `~/.hermes` |

Set them permanently: on Windows with `setx WAKE_TIMEZONE Europe/Berlin` (and the same for the others; new shells only), on Linux in your shell profile or the cron environment.

Without a vault, journals are still written, just to a working folder (`~/.wake/claude-code-journal`, `~/.hermes/journal`).

### Usage

`wake` is installed as a command; `python -m wake` is the same thing. Claude Code, a nightly run that stages a day, writes the note and publishes it:

```bash
.venv/bin/python -m wake cc-journal run --max-days 3
```

The low-level steps, for either agent (`hermes` or `claude-code`):

```bash
.venv/bin/python -m wake journal claude-code status
.venv/bin/python -m wake journal claude-code prepare
.venv/bin/python -m wake journal claude-code commit --day 2026-11-02
```

`status` shows each day as written, staged, pending or empty. `evidence` rebuilds the Evidence section of notes already written.

### Two kinds of sync

- `wake vault-sync` is for timers. It pulls, commits **only** the `journal/` folder and pushes, under a lock. With `--quiet` (what the timers use) it speaks up only after repeated failures. Your other edits are never swept into a commit. If the pull leaves conflicts behind, it refuses to commit and reports them.
- `wake brain sync` is for agents and for you. It commits **everything**, because agents save notes as they work. Commit first, then rebase; a conflict is undone, never written into a note.

### Scheduling

**Claude Code journal on Windows.** `deploy/install-windows-tasks.ps1` registers two Task Scheduler jobs for the current user (no admin): the journal at 01:00, catching up after sleep, and a vault sync every 30 minutes. Both run hidden. Logs go to `~/.wake/logs/`. Rerun the script to update the tasks, or pass `-Remove`.

**Claude Code journal on macOS or Linux.** Run `python -m wake cc-journal run` from cron or a systemd timer, daily:

```cron
0 1 * * *  WAKE_TIMEZONE=Europe/Berlin /path/to/wake/.venv/bin/python -m wake cc-journal run
```

**Hermes journal.** Hermes runs a pre-run script and, if there is work, an agent that writes the draft. `deploy/hermes/daily_journal.py` is that script and `skills/hermes-daily-journal/SKILL.md` is the skill the agent follows. `deploy/deploy-hermes.sh` copies the package, both shims, the skill and the tests to the machine that runs Hermes (set `HOST`, and `HERMES_USER` if it is not `hermes`). Then add a Hermes cron job that runs the script shortly after midnight and a second no-agent job running `vault_sync.py` every 30 minutes. See Hermes's cron documentation for the exact commands.

## Privacy

Transcripts can contain anything you ever typed to an agent. Journal notes and their Evidence sections hold verbatim excerpts of them. The brain itself holds whatever you and your agents write.

- Keep the vault in a **private** repository unless you have read every note.
- No secrets in the vault: the rules tell agents to write where a secret lives, never its value. That is a rule for agents, not a guarantee.
- The model run for Claude Code is locked down: only Read, Write and Edit, confined to a staging folder, no MCP servers, no settings, hooks or CLAUDE.md, no session persistence. Raw transcripts stay on your machine; the model provider sees the staged text, like any other Claude Code session.
- Credential redaction is pattern-based and will miss things.
- Never publish a vault's `journal/` folder without reading it first.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

The tests use synthetic fixtures only. Nothing in them is real conversation text. They include a scan that keeps personal details out of the starter vault.

## Credits

- Andrej Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern is the basis of the vault: raw sources, an agent-maintained wiki, a schema file, and the ingest, query and lint operations.
- [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) by AgriciDaniel (MIT) shaped several ideas: the `hot.md` cache of what's live, splitting the catalog (`index.md`) from the history (`log.md`), an inbox, and immutable raw copies of sources. No code was taken from it.

Wake works with [Hermes](https://github.com/NousResearch/hermes-agent) from Nous Research and [Claude Code](https://claude.com/claude-code) from Anthropic. It is not affiliated with either.

## License

MIT. See [LICENSE](LICENSE).
