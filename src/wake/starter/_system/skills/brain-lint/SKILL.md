---
name: brain-lint
description: "Health check and maintenance pass over the owner's second brain (Obsidian vault): dead links, orphan notes, notes missing from the index, broken frontmatter, merge-conflict markers, a stale hot.md, contradictions, outdated project status, an unprocessed inbox. Fixes what it safely can and reports the rest. Triggers: lint the brain, check the vault, clean up the brain, vault health, tidy notes, maintenance pass."
version: 1.0.0
author: Wake
license: MIT
metadata:
  hermes:
    tags: [brain, lint, maintenance, obsidian]
---

# Brain lint

The vault rules are in `<vault>/AGENTS.md`.

## 1. Mechanical checks

Run `python <vault>/_system/scripts/brain.py lint` (or `wake brain lint`), with `--json` for machine-readable output. It reports:

- **Dead links.** Fix a typo'd target, or create a stub when the subject deserves a note.
- **Orphans** (no inbound links apart from the index). Link each one from the notes it relates to.
- **Missing from `index.md`.** Add a line with a one-line summary under the right heading.
- **Frontmatter problems.** Fill in `type`, `created` and `updated`, using `YYYY-MM-DD`.
- **Unresolved merge conflict markers** (`<<<<<<<`, `>>>>>>>`). These come from a failed pull. Open the note, keep both sides' facts (the owner's wording wins), and remove the markers. Do this first: agents read these notes.
- **Near-empty notes.** Fill them from what the vault knows, or mark them `status: seed`.
- **A stale `hot.md`.** Rebuild it from the last couple of weeks of `log.md` and from current project statuses.
- **Inbox items.** Ingest them with `brain-ingest`, or list them for the owner.

## 2. Judgment checks

These need reading, not just a script. Sample for a routine health check. For a whole-vault audit, enumerate the complete scope first and say which notes you actually read; a sample is not an audit.

- **Contradictions** between notes, especially project status versus the latest `log.md` entries. Flag them with a `> [!warning] Conflict` callout.
- **Stale claims** that newer sources or log entries have superseded. Projects whose `updated` is old, where git or the log shows newer work, should be refreshed from the repo's own docs.
- **Concepts mentioned in several notes that lack their own note.** Create them.
- **Duplicate notes** for the same thing. Merge into one, keep the other name as an alias, and fix the links.
- **Gaps worth a web search.** List them for the owner rather than researching unasked.

## 3. Adding links

Prefer links that already exist in prose: unambiguous mentions, clear author-to-work or project-to-dependency relationships. Don't invent influence or thematic relationships from shared vocabulary, and don't add a reciprocal link merely because an edge exists in the other direction. Wrap a mention without changing the words displayed. Leave sources, quotations, code, paths and identifiers unchanged. A clean dead-link report doesn't mean the connections are meaningful.

## 4. Finish

- `brain.py log lint "Maintenance pass" --agent <you> --detail "<what was fixed>"`
- `brain.py sync -m "<agent>: lint"`

Report to the owner in a few lines what you fixed, what needs their call, and any conflicts. Never delete the owner's notes. Archive them with `status: archived` instead.
