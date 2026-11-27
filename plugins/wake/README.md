# Wake for Hermes (preview)

A regular Hermes CLI plugin, not a memory provider; it does not modify Hermes built-in memory. The standalone Wake CLI and its Claude Code integration remain independent. The plugin subdirectory includes a snapshot of Wake's Python package so a Hermes catalog subdirectory install does not depend on the parent checkout. The snapshot intentionally has plugin-specific safety differences from `src/wake`; reconcile them explicitly when updating it rather than blindly copying the standalone package.

## Install the preview

This is not yet a catalog listing. Select and inspect a **full 40-character commit SHA** from the `hermes-plugin-preview` branch, then use it in the commands below. Without `--ref`, the installer uses the repository's default branch, which may not contain this plugin. Quote the identifier so the shell does not treat `#` as a comment.

```sh
hermes plugins install --no-deps --no-enable --ref <FULL_COMMIT_SHA> 'tdotlab/wake#plugins/wake'
hermes plugins enable --no-allow-tool-override wake
hermes wake setup
```

Replace `<FULL_COMMIT_SHA>` with the actual SHA, without angle brackets. Installation fetches the repository over the network; enabling may reconcile the Hermes dependency environment, including the Windows-only `tzdata` dependency. Check `hermes plugins list` and `hermes wake --help` after enabling. No schedule is created by installation, enablement, or setup.

Every setup choice defaults to no. A new vault requires confirmation and no git commit is made. Separately, connect an existing Wake-style vault containing `AGENTS.md` and `hot.md`; this stores its absolute path in the current profile's `wake/config.json`. Journal commands use that path unless `--vault` is supplied. The vault is not modified by connection. Journal publishing copies only to an explicitly supplied or connected vault; no sync is performed.

You may separately install missing brain, brain-ingest and brain-lint skills into the active profile; existing skills are never overwritten and other vault skills are not installed. Newly installed copies include instructions to read the profile connection. Existing skills are left unchanged and may need their own vault configuration (`WAKE_VAULT`/`BRAIN_VAULT`); the connection is not an environment variable or a global Hermes setting. Each of the three skills uses the vault copy when present, falling back individually to its bundled copy; review vault-provided skills and instructions for unsafe automatic sync before installation. The vault must provide `_system/scripts/brain.py` for workflows that invoke it; connecting does not scaffold or merge files. Setup creates no jobs.

## Manual journal

`hermes wake journal status` inspects the current profile's journal. `hermes wake journal prepare --day YYYY-MM-DD --timezone YOUR_IANA_ZONE` stages a day from `$HERMES_HOME/state.db` (the `--db` and `--journal` options can select other paths). A human can personally write the manifest's `draft_path` using the bundled `guides/journal.md`. Then `hermes wake journal commit --day YYYY-MM-DD --timezone YOUR_IANA_ZONE` validates, adds Evidence, and publishes. `--vault PATH` copies into `PATH/journal/hermes`, including non-Git vaults. Existing different notes are never overwritten or deleted; publication stops with the draft pending. If a vault copy succeeds but a later local write fails, the copy may remain and need manual review before retry. Pass the same `--db`, `--journal`, `--vault`, and `--timezone` on both steps. All plugin journal commands force `--no-sync`; they never push. Empty-day preparation still writes journal state; prepare and commit are explicit write operations. Do not run them without consent.

**Important:** Model-authored journal flow is unavailable: the plugin provides no OS-enforced staging-only model sandbox. Do not use a terminal-enabled agent to draft or publish. The original Hermes skill uses an older deployment path and is not this plugin's guide. No automatic model run, scheduling, vault sync, or unattended operation is provided by this preview.

## Scheduling limitation

The plugin does not create journal schedules. The standalone deployment is separate, not installed by this plugin. No scheduled journal execution has been verified here.

## Privacy

Journal staging and notes contain personal conversation excerpts. Pattern redaction is not exhaustive; providers can see staged content if you send it to a model. Keep vaults private and review Evidence. See the root README and design for limitations.

Plugin code and its subprocesses run with the invoking user's host permissions; Hermes plugin registration is not an OS sandbox. Install and run only in an environment you trust. This preview was tested against Hermes 0.22.2; compatibility with older versions has not been established.
