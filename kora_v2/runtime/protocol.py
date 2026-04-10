"""Shared runtime/protocol metadata for the Kora V2 control plane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kora_v2 import __version__

RUNTIME_NAME = "kora_v2"
API_VERSION = __version__
PROTOCOL_VERSION = "1.0"
PROTOCOL_MAJOR = 1
STATUS_SCHEMA_VERSION = 1

SUPPORTED_INSPECT_TOPICS: tuple[str, ...] = (
    "setup",
    "tools",
    "workers",
    "permissions",
    "session",
    "trace",
    "doctor",
    "phase-audit",
)

SUPPORTED_CAPABILITIES: tuple[str, ...] = (
    "health",
    "status",
    "shutdown",
    "inspect",
    "ws_chat",
    "turn_streaming",
    "auth_relay",
    "session_tracking",
    "trace_persistence",
    "permission_persistence",
    "doctor",
    "phase_audit",
)

WS_MESSAGE_TYPES: tuple[str, ...] = (
    "token",
    "tool_start",
    "tool_result",
    "status",
    "auth_request",
    "question_request",
    "response_complete",
    "interrupt_ack",
    "error",
    "ping",
)


def runtime_metadata() -> dict[str, Any]:
    """Return immutable server identity and capability metadata."""
    return {
        "runtime_name": RUNTIME_NAME,
        "api_version": API_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_major": PROTOCOL_MAJOR,
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "supported_inspect_topics": list(SUPPORTED_INSPECT_TOPICS),
        "capabilities": list(SUPPORTED_CAPABILITIES),
        "ws_message_types": list(WS_MESSAGE_TYPES),
    }


def build_health_payload() -> dict[str, Any]:
    """Return the auth-free health payload."""
    payload = {"status": "ok", "version": API_VERSION}
    payload.update(runtime_metadata())
    return payload


def build_status_payload(
    *,
    session_id: str | None,
    turn_count: int,
    session_active: bool,
    active_sessions: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the authenticated status payload expected by the CLI."""
    payload: dict[str, Any] = {
        "status": "running",
        "version": API_VERSION,
        "runtime_name": RUNTIME_NAME,
        "api_version": API_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_major": PROTOCOL_MAJOR,
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "session_id": session_id,
        "session_active": session_active,
        "turn_count": turn_count,
        "active_sessions": active_sessions,
        "supported_inspect_topics": list(SUPPORTED_INSPECT_TOPICS),
        "capabilities": list(SUPPORTED_CAPABILITIES),
        "server_info": runtime_metadata(),
    }
    if extra:
        payload.update(extra)
    return payload


def extract_runtime_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Pull runtime identity metadata from a status/health payload."""
    metadata: dict[str, Any] = {}

    for source in (
        payload.get("server_info"),
        payload,
    ):
        if not isinstance(source, Mapping):
            continue
        for key in (
            "runtime_name",
            "api_version",
            "version",
            "protocol_version",
            "protocol_major",
            "status_schema_version",
            "supported_inspect_topics",
            "capabilities",
            "ws_message_types",
        ):
            value = source.get(key)
            if value is not None and key not in metadata:
                metadata[key] = value

    if "api_version" not in metadata and "version" in metadata:
        metadata["api_version"] = metadata["version"]

    return metadata


def is_compatible_runtime(payload: Mapping[str, Any]) -> tuple[bool, str]:
    """Validate that a payload belongs to a compatible Kora V2 runtime."""
    metadata = extract_runtime_metadata(payload)
    runtime_name = metadata.get("runtime_name")
    if runtime_name != RUNTIME_NAME:
        return False, f"expected runtime_name={RUNTIME_NAME!r}, got {runtime_name!r}"

    protocol_version = metadata.get("protocol_version")
    protocol_major = metadata.get("protocol_major")
    if protocol_major is None and isinstance(protocol_version, str):
        try:
            protocol_major = int(protocol_version.split(".", 1)[0])
        except ValueError:
            protocol_major = None

    if protocol_major != PROTOCOL_MAJOR:
        return (
            False,
            f"expected protocol_major={PROTOCOL_MAJOR}, got {protocol_major!r}",
        )

    return True, ""
