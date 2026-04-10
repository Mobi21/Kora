"""Test auth_mode setting."""

from kora_v2.core.settings import SecuritySettings


def test_default_auth_mode():
    s = SecuritySettings()
    assert s.auth_mode == "prompt"


def test_trust_all_mode():
    s = SecuritySettings(auth_mode="trust_all")
    assert s.auth_mode == "trust_all"
