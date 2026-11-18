#!/usr/bin/env python3
"""Hermes daily journal (cron pre-run script and commit tool).

  daily_journal.py [prepare]           stage the oldest day that needs a note; days with no conversations get no note
  daily_journal.py commit --day D      validate the draft for day D, add Evidence, publish, copy to the vault, sync
  daily_journal.py status              which days are written, staged or pending
  daily_journal.py evidence [--day D]  add or refresh the Evidence section of written notes

The code is the `wake` package next to this file, deployed from the wake repo by deploy/deploy-hermes.sh.
Change it in the repo, not here.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wake.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main(['journal', 'hermes', *sys.argv[1:]]))
