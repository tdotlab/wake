# Wake for Hermes (preview)

A regular Hermes CLI plugin, not a memory provider; it does not modify Hermes built-in memory. The standalone Wake CLI and its Claude Code integration remain independent. The plugin subdirectory includes a snapshot of Wake's Python package so a Hermes catalog subdirectory install does not depend on the parent checkout. The snapshot intentionally has plugin-specific safety differences from `src/wake`; reconcile them explicitly when updating it rather than blindly copying the standalone package.

## Install the preview

This is not yet a catalog listing. Select and inspect a **full 40-character commit SHA** from the `hermes-plugin-preview` branch, then use it in the commands below. Without `--ref`, the installer uses the repository's default branch, which may not contain this plugin. Quote the identifier so the shell does not treat `#` as a comment.

```sh
hermes plugins install --no-deps --no-enable --ref <FULL_COMMIT_SHA> 'tdotlab/wake#plugins/wake'
hermes plugins enable --no-allow-tool-override wake
hermes wake setup
```

Replace `<FULL_COMMIT_SHA>` with the actual SHA, without angle brackets. Installation fetches the repository over the network; enabling may reconcile the Hermes dependency environment, including the Windows-only `tzdata` dependency. Check `hermes plugins list` and `hermes wake --help` after enabling. Installing or enabling creates no job; `hermes wake setup` offers the nightly job and records your answer.

## Setup

`hermes wake setup` asks about three independent things:

1. **A new starter vault.** Off by default; creating one requires a second confirmation and makes no git commit.
2. **An existing Wake-style vault** containing `AGENTS.md` and `hot.md`. Connecting stores its absolute path in the current profile's `wake/config.json` and does not modify the vault. Journal publishing copies only to that connected vault; nothing is git-synced. You may also install missing brain, brain-ingest and brain-lint skills into the active profile; existing skills are never overwritten and other vault skills are not installed. Each of the three skills uses the vault copy when present, falling back individually to its bundled copy. The vault must provide `_system/scripts/brain.py` for workflows that invoke it; connecting does not scaffold or merge files.
3. **The nightly journal.** **On by default** (press Enter), with an explicit `n` to decline. Setup asks for:
   - fire time, `HH:MM` 24-hour, default `00:30`;
   - the IANA timezone that decides where a journal day ends, defaulted from `WAKE_TIMEZONE`, `TZ`, `/etc/timezone`, or the host zone;
   - delivery, default `local`, or `origin`, `telegram`, `discord`, `signal`, `email`, or another `platform:target` accepted by Hermes cron;
   - model: follow Hermes's configured cron/main model, or choose a provider and model from Hermes's picker inventory. Models are listed in pages of 25 (`n`/`p`); listed or configured does not guarantee account entitlement or a working request. Discovery relies on Hermes's internal inventory API and may be incomplete for custom endpoints; the selected pair is verified only when the job runs.

   Declining creates no job, script, or configuration change. Accepting writes one generated pre-run script to `$HERMES_HOME/scripts/wake_plugin_journal.py` and one enabled cron job named `Wake nightly journal` that attaches the read-only `wake:journal` skill. If a job created by this setup already exists, setup reports it and **leaves it unchanged unless you explicitly answer `y`** to updating its schedule and delivery; it never creates a duplicate. If any other file already occupies the generated script path with different content, setup refuses to overwrite it and creates no job.

   The fire time follows Hermes's configured scheduler timezone (the clock `hermes cron` uses). The journal timezone only controls which completed calendar day the note covers; they may differ.

Remove the job with `hermes wake unschedule` (it removes only the saved job ID) or `hermes cron remove <job-id>`. An older preview job without a recorded ID is not adopted or removed automatically; remove it by ID before setup. The generated script is left in place; delete it manually if nothing else uses it. **Remove the job before disabling or uninstalling the plugin**: a cron job outlives the plugin, and without the plugin the attached `wake:journal` skill is missing and the generated script may no longer find its vendored code.

## Scheduled journal

Hermes runs the pre-run script shortly after the configured time. The script:

- opens `$HERMES_HOME/state.db` read-only and stages one completed day into `$HERMES_HOME/journal/.staging/<day>/`;
- prints a JSON manifest (narrative parts, draft path, tool-evidence path, refs) when there is work;
- prints `NO_JOURNAL_WORK` followed by `{"wakeAgent": false}` when there is nothing to journal. Hermes's wake gate skips the agent run entirely, so an empty day costs no model call.

When there is work, the agent follows `wake:journal`: it reads the staged parts, writes only the manifest's `draft_path`, and runs the generated script's `commit` action. The commit step validates the draft's headings, citation refs, length and credential patterns, rebuilds `## Evidence` from the source, publishes the note, and copies it to the connected vault when one is set. A rejected draft is returned with the exact problems and can be fixed and retried in the same run. Any other failure leaves the day pending for the next run. The plugin never pushes the vault.

**The write boundary is procedural, not an OS sandbox.** The pre-run and commit code do the staging, validation, Evidence and publishing; the skill tells the model to write only `draft_path` and never the journal, its state, or the vault. But the cron agent runs with your host permissions and a terminal, exactly like the standalone Hermes deployment in this repository. Do not run this in a profile you do not trust, and review the skill and generated script before enabling it.

The job fires only while the Hermes cron scheduler is running (`hermes cron status`).

## Manual journal

A human can still run the workflow at a terminal. `hermes wake journal status` inspects the current profile's journal. `hermes wake journal prepare --day YYYY-MM-DD --timezone YOUR_IANA_ZONE` stages a day from `$HERMES_HOME/state.db` (the `--db` and `--journal` options can select other paths). Write the manifest's `draft_path` using the bundled `guides/journal.md`. Then `hermes wake journal commit --day YYYY-MM-DD --timezone YOUR_IANA_ZONE` validates, adds Evidence, and publishes. `--vault PATH` copies into `PATH/journal/hermes`, including non-Git vaults. When a vault copy already exists, Wake updates it only if its bytes still match the prior local journal. A different existing note is preserved and publication stops. The check coordinates Wake's own writers; it is not an atomic compare-and-swap against external editors, so avoid concurrent edits to journal copies. If a vault copy succeeds but a later local write fails, the copy may remain and need manual review before retry. Pass the same `--db`, `--journal`, `--vault`, and `--timezone` on both steps. All plugin journal commands force `--no-sync`; they never push. Empty-day preparation still writes journal state; prepare and commit are explicit write operations. Do not run them without consent.

The bundled `guides/journal.md` remains the human-authored guide; the scheduled agent uses the registered `wake:journal` skill instead because its commit command is the generated profile script.

## Privacy

Journal staging and notes contain personal conversation excerpts. Pattern redaction is not exhaustive; the configured model provider can see staged content. Keep vaults private and review Evidence. See the root README and design for limitations.

## Limitations

The plugin requires in-process plugin isolation; under `plugins.isolation: host`, Hermes skips `ctx.register_cli_command`, so `hermes wake` is unavailable. Plugin code and its subprocesses, including the scheduled journal agent, run with the invoking user's host permissions; Hermes plugin registration is not an OS sandbox. The plugin makes no claim of enforced staging-only confinement. It never git-syncs the vault: run the vault's own sync separately if you want one. Setup and removal call Hermes's own cron store module (`cron.jobs`) and picker inventory rather than public plugin APIs; a future Hermes release could move those seams. Scheduled execution was exercised against a disposable Hermes home (job creation, due firing, empty-day gate, commit and vault copy), not against a user's live profile; the model step depends on the profile's configured provider. This preview was tested against Hermes 0.22.2 and 0.23.4; compatibility with older versions has not been established.
