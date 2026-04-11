"""agent-browser binary wrapper.

Invokes the agent-browser CLI via async subprocess. Returns parsed JSON
output from each command. Never blocks; all calls honor command_timeout_seconds.

Because the exact agent-browser CLI is not fully verified, the command
templates are overridable via CommandTemplate so integrators can adjust.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandTemplate:
    """Argv-template for each browser command.

    Placeholders: {url} {session} {ref} {text} {value} {out} {profile}
    Each value is a list of argv strings with placeholders substituted.
    """

    version: list[str] = field(default_factory=lambda: ["--version"])
    session_open: list[str] = field(
        default_factory=lambda: [
            "session",
            "open",
            "--url",
            "{url}",
            "--profile",
            "{profile}",
        ]
    )
    session_snapshot: list[str] = field(
        default_factory=lambda: [
            "session",
            "snapshot",
            "{session}",
        ]
    )
    session_click: list[str] = field(
        default_factory=lambda: [
            "session",
            "click",
            "{session}",
            "--ref",
            "{ref}",
        ]
    )
    session_type: list[str] = field(
        default_factory=lambda: [
            "session",
            "type",
            "{session}",
            "--ref",
            "{ref}",
            "--text",
            "{text}",
        ]
    )
    session_fill: list[str] = field(
        default_factory=lambda: [
            "session",
            "fill",
            "{session}",
            "--ref",
            "{ref}",
            "--value",
            "{value}",
        ]
    )
    session_screenshot: list[str] = field(
        default_factory=lambda: [
            "session",
            "screenshot",
            "{session}",
            "--out",
            "{out}",
        ]
    )
    session_close: list[str] = field(
        default_factory=lambda: [
            "session",
            "close",
            "{session}",
        ]
    )


_UNSAFE_CHARS = {"\x00"}  # null byte is universally rejected


def _validate_placeholder(value: str, name: str) -> None:
    """Raise ValueError if *value* contains characters unsafe for subprocess argv."""
    for ch in _UNSAFE_CHARS:
        if ch in value:
            raise ValueError(
                f"Placeholder '{name}' contains unsafe character {ch!r}."
            )


@dataclass
class BrowserBinary:
    """Async wrapper around the agent-browser CLI."""

    binary_path: str  # may be empty → try PATH
    profile: str = ""  # empty = default profile
    command_timeout_seconds: int = 30
    commands: CommandTemplate = field(default_factory=CommandTemplate)

    def resolve_binary(self) -> str | None:
        """Return the absolute path to the binary or None if not found."""
        if self.binary_path:
            from pathlib import Path

            p = Path(self.binary_path)
            if p.is_file() and (p.stat().st_mode & 0o111):
                return str(p)
            # Path was set but file not found / not executable — do not fall through
            return None
        # Try PATH
        return shutil.which("agent-browser")

    async def version(self) -> str | None:
        """Run --version. Returns the version string or None on failure."""
        try:
            result = await self._run(self.commands.version, {})
            # The fake binary returns JSON; the real binary may return plain text
            # stored under "version" or the raw stdout.
            if isinstance(result, dict):
                return result.get("version") or result.get("stdout") or str(result)
            return str(result)
        except BrowserCommandError:
            return None

    async def session_open(self, url: str) -> dict[str, Any]:
        """Open a new session. Returns parsed JSON dict."""
        return await self._run(
            self.commands.session_open,
            {"url": url, "profile": self.profile},
        )

    async def session_snapshot(self, session_id: str) -> dict[str, Any]:
        """Take a DOM snapshot of the session. Returns parsed JSON dict."""
        return await self._run(
            self.commands.session_snapshot,
            {"session": session_id},
        )

    async def session_click(self, session_id: str, ref: str) -> dict[str, Any]:
        """Click an element identified by *ref*."""
        return await self._run(
            self.commands.session_click,
            {"session": session_id, "ref": ref},
        )

    async def session_type(
        self, session_id: str, ref: str, text: str
    ) -> dict[str, Any]:
        """Type *text* into the element identified by *ref*."""
        return await self._run(
            self.commands.session_type,
            {"session": session_id, "ref": ref, "text": text},
        )

    async def session_fill(
        self, session_id: str, ref: str, value: str
    ) -> dict[str, Any]:
        """Fill element *ref* with *value* (clears first, then types)."""
        return await self._run(
            self.commands.session_fill,
            {"session": session_id, "ref": ref, "value": value},
        )

    async def session_screenshot(
        self, session_id: str, out_path: str
    ) -> dict[str, Any]:
        """Take a screenshot and write it to *out_path*."""
        return await self._run(
            self.commands.session_screenshot,
            {"session": session_id, "out": out_path},
        )

    async def session_close(self, session_id: str) -> dict[str, Any]:
        """Close the session and release its resources."""
        return await self._run(
            self.commands.session_close,
            {"session": session_id},
        )

    async def _run(
        self, argv_template: list[str], substitutions: dict[str, str]
    ) -> dict[str, Any]:
        """Common subprocess runner. Substitutes placeholders, runs, parses JSON.

        Raises BrowserCommandError on non-zero exit, JSON parse error, or timeout.
        The caller (actions layer) catches this and converts to StructuredFailure.
        """
        binary = self.resolve_binary()
        if binary is None:
            raise BrowserCommandError(
                argv=[],
                exit_code=None,
                stdout="",
                stderr="",
                reason="binary_not_found",
            )

        # Validate and substitute placeholders
        safe_subs: dict[str, str] = {}
        for k, v in substitutions.items():
            _validate_placeholder(v, k)
            safe_subs[k] = v

        argv = [
            part.format_map({**safe_subs, **{k: k for k in ["url", "session", "ref", "text", "value", "out", "profile"] if k not in safe_subs}})
            for part in argv_template
        ]
        # Remove unfilled placeholders (parts that still look like {something})
        # We only substitute what was provided; unknown placeholders become the key itself
        # which is wrong — instead, substitute with empty string for missing keys.
        safe_subs_defaulted = {
            k: safe_subs.get(k, "")
            for k in ["url", "session", "ref", "text", "value", "out", "profile"]
        }
        argv = [part.format_map(safe_subs_defaulted) for part in argv_template]
        # Remove trailing empty args that came from unfilled optional placeholders
        argv = [a for a in argv if a != ""]

        full_argv = [binary, *argv]

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.command_timeout_seconds,
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                raise BrowserCommandError(
                    argv=full_argv,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    reason="timeout",
                )
        except BrowserCommandError:
            raise
        except Exception as exc:
            raise BrowserCommandError(
                argv=full_argv,
                exit_code=None,
                stdout="",
                stderr=str(exc),
                reason="subprocess_error",
            ) from exc

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        if proc.returncode != 0:
            raise BrowserCommandError(
                argv=full_argv,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                reason="non_zero_exit",
            )

        # For --version, the binary may emit plain text rather than JSON.
        # We try JSON first; if that fails and this is a version call we
        # wrap the output in a dict.
        stripped = stdout.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            # Check if this looks like a plain-text version response
            if argv_template == self.commands.version or "--version" in argv_template:
                return {"version": stripped}
            raise BrowserCommandError(
                argv=full_argv,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                reason="json_parse_error",
            )


class BrowserCommandError(Exception):
    def __init__(
        self,
        *,
        argv: list[str],
        exit_code: int | None,
        stdout: str,
        stderr: str,
        reason: str,
    ) -> None:
        self.argv = argv
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.reason = reason
        super().__init__(f"{reason}: {' '.join(argv)}")
