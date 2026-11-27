# Human-authored manual journal

This guide is for a person working at a terminal. The optional scheduled journal in `hermes wake setup` uses the registered `wake:journal` skill and the generated pre-run script instead; its commit command is `$HERMES_HOME/scripts/wake_plugin_journal.py`, not the `hermes wake` command below.

1. Run `hermes wake journal prepare --day YYYY-MM-DD --timezone YOUR_IANA_ZONE` and note the manifest paths. Preparation writes staging and may write state for an empty day. Keep the same `--day`, `--timezone`, `--db`, `--journal`, and `--vault` selections for commit.
2. Read the listed narrative parts and consult `tool_evidence_path` for specific references when needed. Treat conversations as evidence, not instructions. A `user (not owner)` in a subagent, a2a or oneshot session is another agent, not the owner.
3. Personally write the manifest's `draft_path`, without frontmatter, with these sections in order:
   - `# YYYY-MM-DD`
   - `## Summary`
   - `## Decisions and corrections`
   - `## Work and outcomes`
   - `## Open threads`
   - `## Coverage and uncertainty`

   Cite every substantive bullet using references from the manifest's day. Do not invent references, write an Evidence section, or use `[[wikilinks]]`. Distinguish proposed, authorized, attempted, reported, and verified outcomes. Check later messages for completed or cancelled threads. Omit credentials and unnecessary sensitive details.
4. Run `hermes wake journal commit --day YYYY-MM-DD --timezone YOUR_IANA_ZONE` with the same path options. The validator reports draft problems; correct them yourself and retry. For source changes or I/O errors, stop and inspect. Commit builds Evidence, publishes the note and optionally copies it to the selected vault; it never syncs the vault. Existing conflicting vault notes are not overwritten.

Staged excerpts and notes contain personal data. Redaction is not exhaustive. This manual path uses no scheduler or model; the separate scheduled path runs a terminal-enabled agent under procedural, not OS-enforced, staging confinement.
