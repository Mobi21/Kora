"""Unit tests for the Phase 5 first-run wizard helpers."""

from __future__ import annotations

from datetime import time

from kora_v2.adhd.profile import ADHDProfile, ADHDProfileLoader
from kora_v2.cli.first_run import (
    WizardResult,
    _append_env_keys,
    _parse_medication_text,
    _persist,
    _result_to_profile,
    _write_wizard_summary,
)


class TestMedicationParsing:
    def test_basic_line(self):
        result = _parse_medication_text("Adderall XR 20mg 08:00-09:00")
        assert len(result) == 1
        entry = result[0]
        assert entry.name == "Adderall XR"
        assert entry.dose == "20mg"
        assert entry.windows[0].start == time(8, 0)
        assert entry.windows[0].end == time(9, 0)

    def test_multiple_lines(self):
        text = "Adderall XR 20mg 08:00-09:00\nAdderall IR 10mg 13:00-15:00"
        result = _parse_medication_text(text)
        assert len(result) == 2

    def test_ignores_garbage(self):
        result = _parse_medication_text("this is not a medication line")
        assert result == []

    def test_optional_dose(self):
        result = _parse_medication_text("Adderall 08:00-09:00")
        assert len(result) == 1
        assert result[0].name == "Adderall"


class TestResultToProfile:
    def test_peak_crash_windows_mapped(self):
        wr = WizardResult(
            peak_window_label="morning",
            crash_window_label="early afternoon",
        )
        profile = _result_to_profile(wr)
        assert profile.peak_windows == [(6, 9)]
        assert profile.crash_periods == [(13, 15)]

    def test_varies_produces_empty(self):
        wr = WizardResult(
            peak_window_label="varies",
            crash_window_label="varies",
        )
        profile = _result_to_profile(wr)
        assert profile.peak_windows == []
        assert profile.crash_periods == []

    def test_coping_strategies_preserved(self):
        wr = WizardResult(coping_strategies=["timers", "body doubling"])
        profile = _result_to_profile(wr)
        assert "timers" in profile.coping_strategies


class TestPersistence:
    def test_save_round_trips_through_loader(self, tmp_path):
        wr = WizardResult(
            peak_window_label="morning",
            crash_window_label="early afternoon",
            medications_text="Adderall XR 20mg 08:00-09:00",
            coping_strategies=["timers"],
            timezone="America/New_York",
        )

        class _DummyContainer:
            class settings:
                user_tz = "UTC"

        _persist(wr, tmp_path, _DummyContainer())
        loader = ADHDProfileLoader(tmp_path)
        loaded = loader.load()
        assert loaded.peak_windows == [(6, 9)]
        assert len(loaded.medication_schedule) == 1
        assert loaded.medication_schedule[0].name == "Adderall XR"
        assert _DummyContainer.settings.user_tz == "America/New_York"

    def test_loader_returns_defaults_for_missing_file(self, tmp_path):
        loader = ADHDProfileLoader(tmp_path)
        profile = loader.load()
        assert profile.time_correction_factor == 1.5
        assert profile.medication_schedule == []

    def test_loader_roundtrip_with_save(self, tmp_path):
        loader = ADHDProfileLoader(tmp_path)
        original = ADHDProfile(
            time_correction_factor=2.0,
            peak_windows=[(9, 12)],
            coping_strategies=["timers"],
        )
        loader.save(original)
        reloaded = loader.load()
        assert reloaded.time_correction_factor == 2.0
        assert reloaded.peak_windows == [(9, 12)]


class TestWizardSummary:
    def test_summary_written_with_frontmatter_and_prose(self, tmp_path):
        wr = WizardResult(
            name="Mobi",
            pronouns="he/him",
            use_case="I want help keeping my ADHD brain from derailing projects.",
            life_tracking_domains=["medications", "focus"],
        )
        path = _write_wizard_summary(wr, tmp_path)
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "name: Mobi" in text
        assert "pronouns: he/him" in text
        assert "medications" in text and "focus" in text
        assert "derailing projects" in text

    def test_summary_overwrites_on_rerun(self, tmp_path):
        first = WizardResult(name="Mobi", use_case="first reason")
        second = WizardResult(name="Mobi", use_case="updated reason")
        _write_wizard_summary(first, tmp_path)
        _write_wizard_summary(second, tmp_path)
        text = (
            tmp_path / "User Model" / "adhd_profile" / "wizard_summary.md"
        ).read_text(encoding="utf-8")
        assert "updated reason" in text
        assert "first reason" not in text


class TestEnvKeyPersistence:
    def test_minimax_key_appended_when_env_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        env_path = tmp_path / ".env"
        wr = WizardResult(minimax_api_key="mx-key-123")
        _append_env_keys(wr, env_path)
        text = env_path.read_text(encoding="utf-8")
        assert "MINIMAX_API_KEY=mx-key-123" in text

    def test_minimax_key_skipped_when_env_already_set(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("MINIMAX_API_KEY", "existing")
        env_path = tmp_path / ".env"
        wr = WizardResult(minimax_api_key="new-key")
        _append_env_keys(wr, env_path)
        assert not env_path.exists()

    def test_persist_writes_summary_and_profile(self, tmp_path, monkeypatch):
        # Chdir so .env / data/mcp_servers.json land under tmp_path.
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)

        wr = WizardResult(
            name="Mobi",
            pronouns="he/him",
            use_case="keep me honest",
            life_tracking_domains=["focus"],
            peak_window_label="morning",
            crash_window_label="early afternoon",
            minimax_api_key="k1",
            brave_api_key="b1",
        )
        memory_base = tmp_path / "_KoraMemory"
        memory_base.mkdir()

        class _DummyContainer:
            class settings:
                user_tz = "UTC"

        _persist(wr, memory_base, _DummyContainer())

        summary = (
            memory_base / "User Model" / "adhd_profile" / "wizard_summary.md"
        )
        assert summary.exists()
        assert "Mobi" in summary.read_text(encoding="utf-8")

        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "MINIMAX_API_KEY=k1" in env_text
        assert "BRAVE_API_KEY=b1" in env_text

        mcp_config = tmp_path / "data" / "mcp_servers.json"
        assert mcp_config.exists()
        assert "brave_search" in mcp_config.read_text(encoding="utf-8")
