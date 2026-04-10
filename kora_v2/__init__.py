"""Kora V2 — AI companion with genuine personality, emotional states, and comprehensive memory."""

import sys as _sys

# Replace stdlib sqlite3 with pysqlite3 BEFORE any submodule imports aiosqlite.
# The stdlib sqlite3 on macOS lacks enable_load_extension (needed for sqlite-vec).
# pysqlite3-binary has it. The swap MUST happen here — at package import time —
# because aiosqlite binds `import sqlite3` as a module attribute; once that
# binding exists, sys.modules mutation is ineffective for already-imported modules.
try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]
    _sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass  # fall back to stdlib sqlite3 (vector search will be disabled)

__version__ = "2.0.0a1"
