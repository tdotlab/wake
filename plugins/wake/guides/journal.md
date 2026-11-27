# Human-authored manual journal

This preview does not provide a confined model journal flow. Do not ask an agent or model with filesystem or terminal tools to draft, repair, or publish a journal. The following steps are for a human working at a terminal.

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

Staged excerpts and notes contain personal data. Redaction is not exhaustive. No scheduler, automated model run, or staging-only OS sandbox is provided.
