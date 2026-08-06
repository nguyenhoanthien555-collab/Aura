"""
Pytest bootstrap.

Placing conftest.py at the project root puts the root on sys.path, so
tests can `import brain` / `import memory` regardless of the directory
pytest was invoked from.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
