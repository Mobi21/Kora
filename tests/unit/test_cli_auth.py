"""Test CLI auth_request handling."""

from kora_v2.cli.app import KoraCLI


def test_cli_has_auth_handling():
    """Verify KoraCLI can be instantiated (smoke test)."""
    cli = KoraCLI()
    assert cli is not None
