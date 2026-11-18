# wake: agent instructions

- The design is [docs/DESIGN.md](docs/DESIGN.md). Read it before changing anything, and update it when the code changes. Setup, scheduling and commands are in [README.md](README.md).
- The model proposes and code disposes. Never give a model step write access to anything but its staging folder; the script validates the draft and publishes it.
- Transcripts and journals hold personal material. Never commit real excerpts as fixtures; write synthetic ones.
- Code map: `src/wake/core.py` (shared journal machinery, redaction, Evidence), `src/wake/sources/` (one adapter per agent), `cc_runner.py` (the `claude -p` passes), `vault.py` (git sync of the vault's `journal/` folder), `hidden.py` (windowless launcher for Task Scheduler), `cli.py`. The Claude Code prompt is `src/wake/prompts/claude_code_journal.md`; the Hermes skill is `skills/hermes-daily-journal/SKILL.md`.
- Run the tests from a venv with the package installed: `python -m unittest discover -s tests`. Run one test with `-k`, not by module name.
- The speaker tags are `owner`, `agent`, `tool`, `call`, `event` and `other`. Keep them stable; Evidence, the prompts and the tests all rely on them.
- The Hermes journal runs from a deployed copy in `~/.hermes/scripts/wake/` on the Hermes machine. Never edit it there; change it here and run `deploy/deploy-hermes.sh`.
