# wake: design

## What it is for

Every night, turn what you did with your agents into notes that people and agents can read later, without summaries of summaries hardening into false facts. The result is an episodic layer: one note per agent per day that had conversations, in a git-tracked vault. Days with no conversations get no file.

## Shape

```
agent transcripts                       model                         vault
(Hermes state.db,         source        (writes one draft,            (git clone)
 Claude Code .jsonl)  ->  adapter  ->   in a staging folder)  ->  validate + Evidence  ->  journal/<agent>/DATE.md
                          (no model)                              (script)                         |
                                                                                           git pull --rebase, commit, push
```

A source adapter turns one local calendar day of an agent's conversations into a list of messages. The `Journal` class stages them for the model, validates the draft, appends the Evidence section, and publishes the note to the agent's working folder and, when a vault is configured, to the vault.

## Principles

**The model proposes, code disposes.** The model reads a staging folder and writes one draft file. It cannot publish. A script checks the draft and either publishes it or sends it back with the exact problems listed (up to two repairs for Claude Code).

**Evidence, not reflections.** A journal note is already a summary. After a draft passes validation, the commit step appends `## Evidence`: one line per ref the note cites, `ref | speaker | source | HH:MM | excerpt`. The model cannot write this section, and a draft containing one is rejected. Excerpts are redacted and capped at 12,000 characters, the largest staging cap, so Evidence only cuts what staging already cut. Pasted blocks become `[pasted: N chars]`, because a paste is not the user saying something, and `[[` is escaped so excerpts never become wikilinks. `wake journal <agent> evidence` rebuilds the section for notes already written.

**Only the owner's words are the owner's.** Every message carries a speaker: `owner` (what the human typed), `agent`, `tool`, `call` (a tool call, rendered as one line), `event` (notices, interrupts) or `other` (a prompt another agent wrote, such as a subagent brief). The prompts tell the model to attribute decisions and preferences to the owner only when an `owner` block says so.

**Redact before the model sees it.** `core.redact` masks common credential shapes (API keys, tokens, private keys, passwords in URLs, bearer tokens, `key = value` assignments). The validator rejects any draft that still contains a credential-looking value. This is pattern matching, not a guarantee.

**A day with nothing to say gets no note.** `prepare` records an empty day in `.state.json` as done, so the recheck never recreates a file. If conversations arrive later the day is staged and published like any other.

## Message format

Every source provides, per message: `ref` (a citation id unique within the day), `role` (`user`, `assistant`, `tool`, `call`, `event`), `speaker`, `source` (where it happened: a chat platform, `cli`, a project name), `time` (local `HH:MM`), `timestamp` (epoch seconds), `content` (redacted, possibly truncated) and `truncated`. Tool results are staged as evidence for lookup, not as narrative.

## Sources

**Hermes** (`sources/hermes.py`) opens `state.db` read-only and selects messages by timestamp within the local day, so a session spanning several days is split correctly. Sessions whose "user" is the owner (chat platforms and the CLI) count as `owner`; sessions started by other agents (subagent, cron, oneshot) are `other`. Compaction summaries, duplicate copies made by compaction, reasoning and gateway-injected `[System: ...` turns are dropped. Refs look like `s:<session>/m:<id>`.

**Claude Code** (`sources/claude_code.py`) reads `~/.claude/projects/*/*.jsonl`. It keeps the owner's typed messages (records whose origin kind is `human`, plus slash commands), Claude's prose, and one line per tool call (tool name and command or path, no output). It throws away thinking, tool output and compaction summaries. Sidechains and subagents, headless runs (including the journal's own `claude -p` runs) and sessions where the owner never typed are excluded. A forked or resumed session repeats its parent's records, so the earliest session to hold a uuid owns it. Refs look like `c:<session8>/u:<uuid8>`.

To add an agent, write a source class with `agent`, `ref_re`, `ref_hint`, a method that returns one day's messages, and the frontmatter hooks the existing two use, then give it a prompt or skill and a CLI entry. Read the two existing adapters; they are short.

## The Claude Code run

`cc_runner.py` runs the journal unattended: prepare the oldest day that needs a note, run `claude -p` to write a draft, commit, and sync the vault. Each run does at most three days.

The model run is locked down: only Read, Write and Edit, `acceptEdits` inside the staging folder, no MCP servers, no settings files (so no hooks, plugins, CLAUDE.md or allow-lists), no session persistence, no auto-memory. The instructions are a prompt file, `src/wake/prompts/claude_code_journal.md`.

Caps adapt to the day's size: prose and call lines shrink in steps until the narrative fits. A day bigger than 280,000 characters is map-reduced: one pass per slice writes a partial, then a merge pass writes the draft. The validator, Evidence and commit code are shared with Hermes.

**Keep your transcripts long enough.** Claude Code deletes old session files on a schedule (`cleanupPeriodDays`), and Hermes can auto-prune old sessions. A journal that falls behind past that window loses its evidence. The run's own settings keep the cleanup setting high for the model run only; change your own settings if you want a long history.

## The Hermes run

Hermes runs a pre-run script (`deploy/hermes/daily_journal.py`, a shim over this package). With nothing to do it prints a gate line and Hermes skips the agent run entirely. Otherwise it prints a manifest for one completed day, and the agent follows `skills/hermes-daily-journal/SKILL.md`: read the staged parts, write the draft, run `commit`. The agent gets a terminal, so the skill spells out what it must not do, and the transcripts are treated as evidence, never instructions.

## State

- A working folder per agent (`~/.hermes/journal`, `~/.wake/claude-code-journal`) holds `.state.json` (per-day fingerprints and the list of empty days), `.meta/` (per-day manifests), `.revisions/` (replaced notes) and the published notes. Staging is deleted after each commit.
- A written day is rechecked for three days and summarized again only if new messages arrived; after that it is final. To redo an older day on purpose, run `prepare --day YYYY-MM-DD` and then the normal steps.

## Keeping the vault in sync

`vault.py` pulls with rebase and autostash, commits only the `journal/` folder, and pushes, under a lock, with retries for transient network errors. If the pull conflicts, it aborts the rebase so notes never carry conflict markers, and reports failure. Timers keep a failure streak in `.git/wake-sync-state.json` and speak only after two failures in a row, then at most daily, and once on recovery.

## Tried and dropped: the nightly "dream"

An earlier version had a second layer: a nightly pass that read the journals and proposed edits to an agent's long-term memory, with a deterministic checker and a verifier model that could only veto. It ran in shadow mode for about two weeks with nothing applied. It was retired in favor of something much simpler: let the agent clean its own memory files on a schedule with a strong model, and keep long-form detail in the vault. The journals were the part worth keeping, so they are what this repo is.

## Tests

`python -m unittest discover -s tests`. Fixtures are synthetic. The tests cover the staging, validation and Evidence paths for both sources, the Claude Code run with a fake model (repair rounds, slicing, failures), the CLI gate, and the vault sync against real temporary git repositories.
