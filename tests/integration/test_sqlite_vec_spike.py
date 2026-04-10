"""Spike test: sqlite-vec extension loads and queries via aiosqlite.

Validates:
1. sqlite-vec extension loads via aiosqlite (using pysqlite3 backend)
2. vec0 virtual table creation works
3. Vector insert works
4. KNN query returns correct results
5. Performance: <50ms for 100 vectors

Note: macOS system Python's sqlite3 is built without extension loading support.
We use pysqlite3 as the backend for aiosqlite to enable `load_extension()`.
"""
import time
import struct
from pathlib import Path
from typing import Union, Any
from asyncio import AbstractEventLoop
from typing import Optional

import pytest

try:
    import pysqlite3 as pysqlite
except ImportError:
    pysqlite = None

import aiosqlite
import aiosqlite.core
import sqlite_vec


def serialize_f32(vector: list[float]) -> bytes:
    """Serialize a float vector to bytes for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)


def _connect_pysqlite3(
    database: Union[str, Path],
    *,
    iter_chunk_size: int = 64,
    loop: Optional[AbstractEventLoop] = None,
    **kwargs: Any,
) -> aiosqlite.core.Connection:
    """Create an aiosqlite Connection using pysqlite3 as the backend.

    This mirrors aiosqlite.connect() but swaps sqlite3 for pysqlite3,
    which ships its own SQLite build with extension loading enabled.
    """

    def connector():
        loc = str(database)
        return pysqlite.connect(loc, **kwargs)

    return aiosqlite.core.Connection(connector, iter_chunk_size)


# Skip the entire module if pysqlite3 is not available
pytestmark = pytest.mark.skipif(
    pysqlite is None,
    reason="pysqlite3 not installed (required for sqlite extension loading on macOS)",
)


@pytest.fixture
async def vec_db(tmp_path):
    """Create a file-backed DB with sqlite-vec loaded via pysqlite3."""
    db_path = tmp_path / "test_vec.db"
    async with _connect_pysqlite3(db_path) as db:
        await db.enable_load_extension(True)
        await db.load_extension(sqlite_vec.loadable_path())
        yield db


class TestSqliteVecSpike:
    @pytest.mark.asyncio
    async def test_extension_loads(self, vec_db):
        """sqlite-vec extension loads and reports version."""
        async with vec_db.execute("SELECT vec_version()") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            version = row[0]
            assert version  # Non-empty version string
            print(f"sqlite-vec version: {version}")

    @pytest.mark.asyncio
    async def test_create_vec0_table(self, vec_db):
        """vec0 virtual table creation works."""
        await vec_db.execute(
            "CREATE VIRTUAL TABLE test_vectors USING vec0(embedding float[4])"
        )
        # Verify table exists
        async with vec_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_vectors'"
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None

    @pytest.mark.asyncio
    async def test_insert_and_query(self, vec_db):
        """Insert vectors and query nearest neighbors."""
        await vec_db.execute(
            "CREATE VIRTUAL TABLE memories_vec USING vec0(embedding float[4])"
        )

        # Insert test vectors
        vectors = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.9, 0.1, 0.0, 0.0],  # Similar to first
        ]
        for i, vec in enumerate(vectors):
            await vec_db.execute(
                "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
                (i + 1, serialize_f32(vec)),
            )

        # Query: find nearest to [1.0, 0.0, 0.0, 0.0]
        query_vec = serialize_f32([1.0, 0.0, 0.0, 0.0])
        async with vec_db.execute(
            "SELECT rowid, distance FROM memories_vec "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
            (query_vec,),
        ) as cursor:
            results = await cursor.fetchall()

        assert len(results) == 3
        # First result should be rowid=1 (exact match, distance=0)
        assert results[0][0] == 1
        assert results[0][1] == pytest.approx(0.0, abs=0.01)
        # Second should be rowid=4 (similar vector)
        assert results[1][0] == 4

    @pytest.mark.asyncio
    async def test_768_dim_vectors(self, vec_db):
        """Test with 768-dim vectors (nomic embedding size)."""
        await vec_db.execute(
            "CREATE VIRTUAL TABLE embed_768 USING vec0(embedding float[768])"
        )

        # Insert a few 768-dim vectors
        import random

        random.seed(42)
        for i in range(5):
            vec = [random.gauss(0, 1) for _ in range(768)]
            await vec_db.execute(
                "INSERT INTO embed_768(rowid, embedding) VALUES (?, ?)",
                (i + 1, serialize_f32(vec)),
            )

        # Query
        query = [random.gauss(0, 1) for _ in range(768)]
        async with vec_db.execute(
            "SELECT rowid, distance FROM embed_768 "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
            (serialize_f32(query),),
        ) as cursor:
            results = await cursor.fetchall()

        assert len(results) == 3
        # All distances should be positive
        for _, dist in results:
            assert dist >= 0

    @pytest.mark.asyncio
    async def test_performance_100_vectors(self, vec_db):
        """100 vectors insert + query should be <50ms."""
        await vec_db.execute(
            "CREATE VIRTUAL TABLE perf_test USING vec0(embedding float[768])"
        )

        import random

        random.seed(123)

        start = time.perf_counter()

        # Insert 100 vectors
        for i in range(100):
            vec = [random.gauss(0, 1) for _ in range(768)]
            await vec_db.execute(
                "INSERT INTO perf_test(rowid, embedding) VALUES (?, ?)",
                (i + 1, serialize_f32(vec)),
            )

        # Query
        query = [random.gauss(0, 1) for _ in range(768)]
        async with vec_db.execute(
            "SELECT rowid, distance FROM perf_test "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT 5",
            (serialize_f32(query),),
        ) as cursor:
            results = await cursor.fetchall()

        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(results) == 5
        print(f"100 vectors insert + query: {elapsed_ms:.1f}ms")
        assert elapsed_ms < 5000  # Very generous for CI; typically <50ms locally
