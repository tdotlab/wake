#!/usr/bin/env python3
"""Pull, commit and push the journal folder of the vault clone (WAKE_VAULT, default ~/vault), for a half-hourly
no-agent Hermes cron job. It prints only when a failure streak starts, persists a day, or ends, and the job
delivers whatever it prints to you. A failed sync still exits 0, so Hermes's own failure notice (which would
fire every 30 minutes) is left for real crashes. Deployed from the wake repo by deploy/deploy-hermes.sh."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wake.cli import main  # noqa: E402

if __name__ == '__main__':
    main(['vault-sync', '--quiet'])
    sys.exit(0)
