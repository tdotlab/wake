#!/usr/bin/env bash
# Deploy to the machine that runs Hermes: the wake package, the two shims (journal, vault sync), the daily-journal
# skill and the tests, into ~/.hermes of the Hermes user. Backs up what it replaces to ~/.hermes/backups/, then
# runs the tests there. Set HOST to an ssh host that reaches the machine, and HERMES_USER (default: hermes) to the
# account that owns ~/.hermes. That account must be reachable with passwordless `sudo -n -u <user>`; if you
# log in as the Hermes user itself, drop the `sudo -n -u` part below.
set -euo pipefail
HOST=${HOST:?set HOST to the ssh host that runs Hermes}
HERMES_USER=${HERMES_USER:-hermes}
repo=$(cd "$(dirname "$0")/.." && pwd)
stamp=$(date +%Y-%m-%d-%H%M%S)
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT

mkdir -p "$build/scripts/journal-tests" "$build/skills/note-taking/daily-journal"
cp -r "$repo/src/wake" "$build/scripts/wake"
find "$build" -name __pycache__ -prune -exec rm -rf {} +
cp "$repo/deploy/hermes/daily_journal.py" "$repo/deploy/hermes/vault_sync.py" "$build/scripts/"
cp "$repo"/tests/*.py "$build/scripts/journal-tests/"
cp "$repo/skills/hermes-daily-journal/SKILL.md" "$build/skills/note-taking/daily-journal/SKILL.md"

tar -czf - -C "$build" scripts skills | ssh "$HOST" "sudo -n -u $HERMES_USER bash -c '
  set -euo pipefail
  cd ~/.hermes
  mkdir -p backups
  old=\"scripts/daily_journal.py scripts/journal-tests skills/note-taking/daily-journal\"
  [ -d scripts/wake ] && old=\"\$old scripts/wake\"
  [ -f scripts/vault_sync.py ] && old=\"\$old scripts/vault_sync.py\"
  tar -czf backups/journal-before-$stamp.tar.gz \$old
  rm -rf scripts/wake scripts/journal-tests
  tar -xzf - -C ~/.hermes
  chmod 700 scripts/daily_journal.py scripts/vault_sync.py
  echo \"Deployed; backup is ~/.hermes/backups/journal-before-$stamp.tar.gz\"
  cd scripts && python3 -m unittest discover -s journal-tests 2>&1 | tail -3
'"
