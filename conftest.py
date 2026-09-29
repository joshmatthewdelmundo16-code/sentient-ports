"""
pytest conftest.py — adds platform/ to sys.path so that
`backend.app.*` imports resolve correctly when pytest is run
from the platform/ directory.
"""

import sys
import os
from pathlib import Path

# platform/ directory (where this conftest.py lives)
_PLATFORM_DIR = Path(__file__).parent.resolve()

if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))
