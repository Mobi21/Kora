"""Tests for desktop Electron view-model API routes."""

from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kora_v2.core.di import Container
from kora_v2.core.settings import Settings
from kora_v2.daemon.server import create_app

TEST_TOKEN = "desktop-test-token"


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    token_path = tmp_path / "api_token"
    token_path.write_text(TEST_TOKEN)
    settings = Settings()
    settings.security.api_token_path = str(token_path)
    with patch("kora_v2.core.di.MiniMaxProvider"):
        container = Container(settings)
    return TestClient(create_app(container))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def test_desktop_api_allows_localhost_dev_preflight(client: TestClient) -> None:
    response = client.options(
        "/api/v1/desktop/status",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_desktop_status_is_authenticated(client: TestClient) -> None:
    assert client.get("/api/v1/desktop/status").status_code == 401
    response = client.get("/api/v1/desktop/status", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["host"] == "127.0.0.1"
    assert body["vault"]["obsidian_facing"] is True


def test_desktop_vault_context_reads_current_life_os_schema(client: TestClient) -> None:
    data_dir = Settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(data_dir / "operational.db") as conn:
        conn.executescript(
            """
            CREATE TABLE context_packs (
                id TEXT PRIMARY KEY,
                calendar_entry_id TEXT,
                item_id TEXT,
                title TEXT NOT NULL,
                pack_type TEXT NOT NULL,
                status TEXT NOT NULL,
                content_path TEXT,
                summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            );
            CREATE TABLE future_self_bridges (
                id TEXT PRIMARY KEY,
                bridge_date TEXT NOT NULL,
                source_day_plan_id TEXT,
                load_assessment_id TEXT,
                summary TEXT NOT NULL,
                carryovers TEXT NOT NULL,
                first_moves TEXT NOT NULL,
                content_path TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT
            );
            INSERT INTO context_packs
                (id, title, pack_type, status, content_path, created_at, updated_at)
                VALUES ('pack-1', 'Portal prep', 'medical', 'ready', '/tmp/pack.md', '2026-04-29T12:00:00+00:00', '2026-04-29T12:00:00+00:00');
            INSERT INTO future_self_bridges
                (id, bridge_date, summary, carryovers, first_moves, content_path, created_at)
                VALUES ('bridge-1', '2026-04-30', 'Keep this visible.', '[]', '[]', '/tmp/bridge.md', '2026-04-29T12:00:00+00:00');
            """
        )

    response = client.get("/api/v1/desktop/vault/context", headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert body["context_packs"][0]["artifact_path"] == "/tmp/pack.md"
    assert body["future_bridges"][0]["artifact_path"] == "/tmp/bridge.md"


def test_desktop_today_empty_state(client: TestClient) -> None:
    response = client.get(
        "/api/v1/desktop/today",
        params={"date": date(2026, 4, 29).isoformat()},
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-04-29"
    assert body["now"]["empty_label"]
    assert body["timeline"] == []


def test_desktop_repair_preview_is_non_mutating(client: TestClient) -> None:
    response = client.post(
        "/api/v1/desktop/repair/preview",
        json={"date": "2026-04-29", "change_type": "make_smaller"},
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mutates_state"] is False
    assert body["actions"][0]["requires_confirmation"] is True


def test_desktop_settings_patch_persists(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/desktop/settings",
        json={"theme_family": "quiet-dark", "density": "compact"},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json()["theme_family"] == "quiet-dark"

    followup = client.get("/api/v1/desktop/settings", headers=_auth())
    assert followup.status_code == 200
    assert followup.json()["density"] == "compact"


def test_desktop_calendar_preview_does_not_mutate(client: TestClient) -> None:
    response = client.post(
        "/api/v1/desktop/calendar/preview",
        json={
            "operation": "move",
            "event_id": "missing-event",
            "starts_at": "2026-04-29T10:00:00+00:00",
            "ends_at": "2026-04-29T11:00:00+00:00",
        },
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mutates_state"] is False
    assert body["requires_confirmation"] is True


def test_desktop_calendar_apply_returns_unavailable_until_wired(client: TestClient) -> None:
    response = client.post(
        "/api/v1/desktop/calendar/apply",
        json={"operation": "cancel", "event_id": "x"},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"


def test_desktop_medication_returns_unconfigured_when_missing(client: TestClient) -> None:
    response = client.get(
        "/api/v1/desktop/medication",
        params={"date": "2026-04-29"},
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["health"] == "unconfigured"


def test_desktop_routines_returns_health_state(client: TestClient) -> None:
    response = client.get(
        "/api/v1/desktop/routines",
        params={"date": "2026-04-29"},
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["health"] in {"ok", "unconfigured", "unavailable"}


def test_desktop_autonomous_view(client: TestClient) -> None:
    response = client.get("/api/v1/desktop/autonomous", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert "active" in body
    assert "queued" in body
    assert "open_decisions" in body


def test_desktop_integrations_lists_capabilities(client: TestClient) -> None:
    response = client.get("/api/v1/desktop/integrations", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    kinds = {item["kind"] for item in body["integrations"]}
    assert {"workspace", "vault", "browser"}.issubset(kinds)


def test_desktop_settings_validate_rejects_unknown_theme(client: TestClient) -> None:
    response = client.post(
        "/api/v1/desktop/settings/validate",
        json={"theme_family": "neon-synthwave"},
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(issue["path"] == "theme_family" for issue in body["issues"])


def test_desktop_settings_validate_passes_valid_patch(client: TestClient) -> None:
    response = client.post(
        "/api/v1/desktop/settings/validate",
        json={
            "theme_family": "low-stimulation",
            "density": "balanced",
            "motion": "reduced",
            "calendar_default_view": "week",
            "chat_panel_width": 380,
        },
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["issues"] == []
