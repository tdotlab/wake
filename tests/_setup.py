"""Make `wake` importable from the repo (src/) or from a deployed folder (the tests' parent)."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
for candidate in (HERE.parent / 'src', HERE.parent):
    if (candidate / 'wake' / '__init__.py').is_file():
        sys.path.insert(0, str(candidate))
        PACKAGE_ROOT = candidate
        break
else:
    raise ImportError('wake package not found next to the tests')
