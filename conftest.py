"""
pytest conftest.py

Two jobs:

1. Put platform/ on sys.path so `backend.app.*` imports resolve when pytest is run from
   the platform/ directory.

2. Force the application database to a throwaway in-memory SQLite database *before* any
   test module imports application code.

Why (2) matters — D24
---------------------
`backend.app.config.settings` reads DATABASE_URL from the environment and then falls back
to the repository-root `.env`. That `.env` may point at a real, shared, managed PostgreSQL.
Individual test modules did guard themselves with `os.environ.setdefault(...)`, but three
did not, and `setdefault` at module scope only protects whichever module pytest happens to
import first — `backend.app.persistence.database` binds its engine at first import and
every later test shares it. A single collection-order change was enough to point the whole
suite at a shared database.

conftest.py is imported before any test module, so setting it here is the only placement
that actually guarantees isolation. PostgreSQL tests are unaffected: they read
TEST_DATABASE_URL and build their own engine, which is a separate, explicitly-opted-in
knob (see tests/test_d15_postgresql.py).
"""

import os
import sys
from pathlib import Path

# platform/ directory (where this conftest.py lives)
_PLATFORM_DIR = Path(__file__).parent.resolve()

if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))

# Must happen before backend.app.config.settings is imported by anything.
_configured = os.environ.get("DATABASE_URL", "")
if _configured and not _configured.startswith("sqlite"):
    # An explicitly exported non-SQLite DATABASE_URL is almost certainly a mistake in a
    # test run. Fail loudly rather than silently writing to someone's shared database.
    raise RuntimeError(
        "DATABASE_URL points at a non-SQLite database "
        f"({_configured.split('://', 1)[0]}://…) while running the test suite. "
        "Tests create, drop and mutate data; refusing to run against it. "
        "Unset DATABASE_URL, or set it to a sqlite:// URL. "
        "PostgreSQL coverage is opted into separately via TEST_DATABASE_URL."
    )

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# Keep test artifacts out of the developer's real storage directory.
os.environ.setdefault("STORAGE_ROOT", str(_PLATFORM_DIR.parent / "storage_test_tmp"))
os.environ.setdefault("ENVIRONMENT", "test")
# D26: pre-D26 tests exercise the single-tenant demo; the multi-port network seed and fast
# password hashing are opted into explicitly by the D26/D27 tests.
os.environ.setdefault("DEMO_NETWORK_SEED", "false")
os.environ.setdefault("AUTH_SCRYPT_N", "1024")
os.environ.setdefault("AUTH_SCRYPT_P", "1")
