# wake: design

## What it is for

Every night, turn what you did with your agents into notes that people and agents can read later, without summaries of summaries hardening into false facts. The result is an episodic layer: one note per agent per day that had conversations, in a git-tracked vault. Days with no conversations get no file.

## Shape

```
agent transcripts                       model                         vault
(Hermes state.db,         source        (writes one draft,            (git clone)
 Claude Code .jsonl)  ->  adapter  ->   in a staging folder)  ->  validate + Evidence  ->  journal/<agent>/YYYY-MM/DATE.md
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

**Where each agent's instructions live.** They differ because the two agents are driven differently. Wake runs Claude Code itself (`claude -p`), so its instructions are a prompt file the runner loads: `src/wake/prompts/claude_code_journal.md`. Hermes runs its own agent from its own cron, so its instructions are a Hermes skill that gets deployed onto the Hermes machine: `skills/hermes-daily-journal/SKILL.md`. The two share the validator, so they describe the same note format; keep them in step when the format changes. A third agent gets whichever of the two shapes matches how it is run.

To add an agent, write a source class with `agent`, `ref_re`, `ref_hint`, a method that returns one day's messages, and the frontmatter hooks the existing two use, then give it a prompt or skill and a CLI entry. Read the two existing adapters; they are short.

## The Claude Code run

`cc_runner.py` runs the journal unattended: prepare the oldest day that needs a note, run `claude -p` to write a draft, commit, and sync the vault. Each run does at most three days.

The model run is locked down: only Read, Write and Edit, `acceptEdits` inside the staging folder, no MCP servers, no settings files (so no hooks, plugins, CLAUDE.md or allow-lists), no session persistence, no auto-memory. The instructions are a prompt file, `src/wake/prompts/claude_code_journal.md`.

Caps adapt to the day's size: prose and call lines shrink in steps until the narrative fits. A day bigger than 280,000 characters is map-reduced: one pass per slice writes a partial, then a merge pass writes the draft. The validator, Evidence and commit code are shared with Hermes.

**Keep your transcripts long enough.** Claude Code deletes old session files on a schedule (`cleanupPeriodDays`), and Hermes can auto-prune old sessions. A journal that falls behind past that window loses its evidence. The run's own settings keep the cleanup setting high for the model run only; change your own settings if you want a long history.

## The Hermes run

Hermes runs a pre-run script (`deploy/hermes/daily_journal.py`, a shim over this package). With nothing to do it prints a gate line and Hermes skips the agent run entirely. Otherwise it prints a manifest for one completed day, and the agent follows `skills/hermes-daily-journal/SKILL.md`: read the staged parts, write the draft, run `commit`. The agent gets a terminal, so the skill spells out what it must not do, and the transcripts are treated as evidence, never instructions.

## Hermes plugin (preview)

`plugins/wake` is an independent regular Hermes plugin subdirectory, not a memory provider. It bundles the standalone package snapshot. Registration adds one CLI command and one read-only skill; loading it does not start work. Setup can create a starter, store an existing Wake-style vault path in the current profile, and install missing profile skills without overwriting existing ones. Newly installed skills read the profile connection; preexisting skills remain untouched. The journal CLI passes the connected vault, and the plugin publisher copies into explicitly selected Git or non-Git vaults and never syncs. It does not modify Hermes built-in memory.

The preview installer must use the `plugins/wake` subdirectory and an explicit full commit SHA: the default branch may not contain the plugin. The bundled CLI's post-init guidance points back to plugin setup rather than standalone scheduling or a separately installed `wake` executable. Journal setup needs system IANA timezone data or the Python `tzdata` package in Hermes's environment; missing data is reported separately from an invalid zone where possible.

The plugin's manual journal guide is human-authored. Setup separately offers an enabled nightly job by default, with an explicit decline and configurable time, journal timezone, delivery, and model (follow cron/main or choose a provider/model from Hermes's picker inventory). The inventory is not proof of entitlement or a successful model call. The scheduler timezone determines fire time independently of the journal day boundary, and setup discloses that distinction before asking for the time. Accepting writes a generated pre-run script into the profile's `scripts/` folder and a cron job that attaches the read-only `wake:journal` skill. The script stages one day and prints a manifest, or prints `{"wakeAgent": false}` on an empty day so Hermes skips the agent. A non-empty manifest wakes a terminal-enabled agent that follows the skill, writes only the draft, and invokes the script's validating `commit` action, which builds Evidence and publishes. That write boundary is procedural, not an OS sandbox: the agent has the invoking user's host permissions. The plugin never syncs the vault. Setup records the job ID; `hermes wake unschedule` removes only that ID. The bulk evidence rewrite action is not exposed through the plugin.

## State

- A working folder per agent (`~/.hermes/journal`, `~/.wake/claude-code-journal`) holds `.state.json` (per-day fingerprints and the list of empty days), `.meta/` (per-day manifests), `.revisions/` (replaced notes) and the published notes. Staging is deleted after each commit.
- A written day is rechecked for three days and summarized again only if new messages arrived; after that it is final. To redo an older day on purpose, run `prepare --day YYYY-MM-DD` and then the normal steps.

## Keeping the vault in sync

`vault.py` pulls with rebase and autostash, commits only the `journal/` folder, and pushes, under a lock, with retries for transient network errors. If the pull conflicts, it aborts the rebase so notes never carry conflict markers, and reports failure. `--autostash` can exit 0 while its stash pop leaves conflicts in the working tree, so after a pull it checks for unmerged paths and refuses to commit if there are any. Timers keep a failure streak in `.git/wake-sync-state.json` and speak only after two failures in a row, then at most daily, and once on recovery.

## The starter vault

The journals are one layer of a larger idea: a plain-Markdown vault that every agent reads before guessing and writes to when it learns something durable. `wake init` creates one from `src/wake/starter/`; the journals file themselves into its `journal/` folder. The vault half is deliberately thin, because Obsidian and git already do the hard parts.

- **Rules live in the vault, not in Wake.** `AGENTS.md` at the vault root is the contract (folder map, what to write, how to link, bookkeeping, rules with teeth). `CLAUDE.md` is `@AGENTS.md`. Any agent that can read files can follow it; Wake's skills just make that convenient for Claude Code and Hermes.
- **Self-contained.** `_system/scripts/brain.py` is a byte copy of `src/wake/brain.py`, standard library only, so a vault keeps working with Wake uninstalled. `wake brain` runs the same module. The vault is found from `--vault`, `$WAKE_VAULT`, `$BRAIN_VAULT`, the script's own location, the nearest ancestor holding `AGENTS.md` and `hot.md`, then `~/vault`.
- **Three skills** (`brain`, `brain-ingest`, `brain-lint`) hold the procedures. `install-skills` copies them where agents look, preferring the vault's own copy so the owner's edits are what ships.
- **The owner's space is read-only to agents.** `daily/` and `notes/` are open to every agent to read and are never rewritten. `inbox/` is the drop zone, `raw/` is append-only.
- **Journals are data.** The rules tell agents a journal quotes conversations and may contain anything, so nothing in one is an instruction. Lint schema-checks journal notes (Wake writes `created` and `updated`) but exempts them from the near-empty check.
- **Lint is advice, with three exceptions.** `--strict` fails only on dead links, frontmatter errors and unresolved conflict markers. Orphans, index gaps and a stale `hot.md` are reported and never fail a run.

`brain.py sync` commits first and then rebases, without autostash, because a rebase over a dirty tree is what lets stash-pop conflict markers get swept into a commit by `git add -A`. On a conflict it aborts the rebase and leaves the local commit as it was. `vault.py` keeps autostash (it must, since it commits only `journal/` and leaves other edits dirty) and guards against the same failure by checking for unmerged paths after the pull.

## Tried and dropped: the nightly "dream"

An earlier version had a second layer: a nightly pass that read the journals and proposed edits to an agent's long-term memory, with a deterministic checker and a verifier model that could only veto. It ran in shadow mode for about two weeks with nothing applied. It was retired in favor of something much simpler: let the agent clean its own memory files on a schedule with a strong model, and keep long-form detail in the vault. The journals were the part worth keeping, so they are what this repo is.

## Tests

`python -m unittest discover -s tests`. Fixtures are synthetic. The tests cover the staging, validation and Evidence paths for both sources, the Claude Code run with a fake model (repair rounds, slicing, failures), the CLI gate, and the vault sync against real temporary git repositories. The starter vault is covered by `test_brain.py` (lint, new, find, log, stats, `brain.py sync` against a bare remote including a conflict) and `test_scaffold.py` (init layout and placeholders, `--merge` never overwriting, `install-skills`, and a scan that keeps personal strings out of the starter).
