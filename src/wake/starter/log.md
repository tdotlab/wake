---
type: meta
created: {{today}}
updated: {{today}}
tags:
  - meta
  - log
---

# Log

Append-only history, newest first. Add entries with `python _system/scripts/brain.py log <op> "<title>" --agent <name>`. Every entry starts `## [YYYY-MM-DD] op | Title`, so `grep "^## \[" log.md | head` gives the latest.

<!-- entries -->

## [{{today}}] setup | Vault created
- agent: wake init
- Starter vault from Wake: rules in AGENTS.md, skills and templates in _system/.
