# brain

{{owner}}'s second brain: a plain-Markdown vault that {{owner}} and every agent (Claude Code, Codex, Hermes, and anything else that can read files) read and write. It follows Karpathy's LLM Wiki pattern and was created with [Wake](https://github.com/tdotlab/wake).

- Open it in Obsidian with "Open folder as vault". The landing page is `Home.md`.
- The rules for agents are in `AGENTS.md`; `CLAUDE.md` points there.
- Skills are in `_system/skills/` (`brain`, `brain-ingest`, `brain-lint`). `wake install-skills --claude --hermes` copies them where those agents look.
- The helper is `python _system/scripts/brain.py lint|find|log|new|stats|sync` (standard library only), or `wake brain ...` if Wake is installed.
- Nightly journals of what the agents did land in `journal/` when Wake's journal jobs are scheduled.
- Sync between machines is git: `brain.py sync` commits, pulls with rebase and pushes.

Keep it private. Journals quote real conversations, and no secret belongs in here, ever: notes say where secrets live, never their values.
