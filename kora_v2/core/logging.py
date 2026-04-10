"""Kora V2 — Structured logging with structlog.

* JSON output to rotating daily log files (7-day retention)
* Per-request correlation IDs via ``contextvars``
* ``setup_logging()`` wires stdlib + structlog in one call
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import uuid
from contextvars import ContextVar
from pathlib import Path

import structlog

# ── Correlation ID ───────────────────────────────────────────────────────

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    """Generate a fresh correlation ID and bind it to the current context."""
    cid = uuid.uuid4().hex[:12]
    correlation_id_var.set(cid)
    return cid


def get_correlation_id() -> str:
    """Return the current correlation ID (empty string if unset)."""
    return correlation_id_var.get()


# ── structlog processors ────────────────────────────────────────────────

def _add_correlation_id(
    logger: logging.Logger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: dict,
) -> dict:
    """Inject the correlation ID into every log entry."""
    cid = correlation_id_var.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


# ── Secret scrubbing ─────────────────────────────────────────────────────

_SECRET_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"sk-ant-[a-zA-Z0-9\-_]{20,}"),      # Anthropic explicit (match before generic sk-)
    re.compile(r"sk-cp-[a-zA-Z0-9\-_]{20,}"),       # MiniMax CP keys
    re.compile(r"sk-[a-zA-Z0-9\-_]{20,}"),          # Anthropic/MiniMax/OpenAI generic
    re.compile(r"Bearer\s+[a-zA-Z0-9._\-]+"),       # HTTP bearer tokens
    re.compile(r"(?i)api[_-]?key[\"']?\s*[:=]\s*[\"']?[a-zA-Z0-9\-_]{16,}"),   # api_key=... style
    re.compile(r"(?i)authorization[\"']?\s*[:=]\s*[\"']?[a-zA-Z0-9\-_\s]{16,}"),
)


def _scrub_secrets(
    logger: logging.Logger,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: dict,
) -> dict:
    """Redact known secret patterns in event values (recursive)."""
    def _scrub(value):
        if isinstance(value, str):
            scrubbed = value
            for pattern in _SECRET_PATTERNS:
                scrubbed = pattern.sub("[REDACTED]", scrubbed)
            return scrubbed
        if isinstance(value, dict):
            return {k: _scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(_scrub(v) for v in value)
        return value

    return {k: _scrub(v) for k, v in event_dict.items()}


# ── Setup ────────────────────────────────────────────────────────────────

_DEFAULT_LOG_DIR = Path("~/.kora/logs").expanduser()


def setup_logging(
    log_dir: Path | None = None,
    *,
    level: int = logging.INFO,
    console: bool = False,
) -> None:
    """Configure structlog + stdlib logging.

    Parameters
    ----------
    log_dir:
        Directory for ``kora.log``.  Defaults to ``~/.kora/logs/``.
    level:
        Root log level (default ``INFO``).
    console:
        If *True*, also emit human-readable logs to stderr.
    """
    log_dir = log_dir or _DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "kora.log"

    # ── stdlib handler: daily rotation, 7-day retention ──────────
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setLevel(level)

    handlers: list[logging.Handler] = [file_handler]

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        handlers.append(stream_handler)

    logging.basicConfig(
        format="%(message)s",
        level=level,
        handlers=handlers,
        force=True,
    )

    # ── structlog shared processors ──────────────────────────────
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_correlation_id,
        _scrub_secrets,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # ── structlog configuration ──────────────────────────────────
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Attach a structlog-aware formatter to every stdlib handler so that
    # entries coming from *either* structlog or plain ``logging`` go
    # through the same JSON pipeline.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    for handler in handlers:
        handler.setFormatter(formatter)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Typical usage::

        from kora_v2.core.logging import get_logger
        log = get_logger(__name__)
        log.info("booted", version="2.0")
    """
    return structlog.get_logger(name)
