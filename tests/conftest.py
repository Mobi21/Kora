"""Root conftest — patches pysqlite3 as sqlite3 before any test imports.

pysqlite3 provides enable_load_extension() which macOS's built-in
sqlite3 module lacks. This must happen before aiosqlite or any other
code imports the sqlite3 module.
"""

import sys

try:
    import pysqlite3 as _pysqlite3  # type: ignore[import-untyped]
    sys.modules["sqlite3"] = _pysqlite3
except ImportError:
    pass
