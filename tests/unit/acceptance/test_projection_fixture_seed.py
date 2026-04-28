import sqlite3
from pathlib import Path

from tests.acceptance import automated


def test_seed_projection_acceptance_fixtures_creates_missing_projection_db(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "repo"
    accept_dir = tmp_path / "acceptance"
    (project_root / "data").mkdir(parents=True)

    monkeypatch.setattr(automated, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(automated, "ACCEPT_DIR", accept_dir)

    automated._seed_projection_acceptance_fixtures()

    db_path = project_root / "data" / "projection.db"
    assert db_path.exists()
    with sqlite3.connect(str(db_path)) as db:
        rows = db.execute(
            "SELECT id, importance, entities, status FROM memories "
            "WHERE id LIKE 'acceptance-dedup-local-first-%' "
            "ORDER BY id"
        ).fetchall()

    assert rows == [
        (
            "acceptance-dedup-local-first-a",
            0.95,
            '["Jordan", "Alex", "Mochi"]',
            "active",
        ),
        (
            "acceptance-dedup-local-first-b",
            0.2,
            '["Jordan"]',
            "active",
        ),
    ]
    assert (
        accept_dir / "memory" / "Long-Term" / "acceptance-dedup-local-first-a.md"
    ).exists()
    assert (
        accept_dir / "memory" / "Long-Term" / "acceptance-dedup-local-first-b.md"
    ).exists()
