"""Vault capability pack — filesystem mirror target for clips and notes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kora_v2.capabilities.base import (
    Action,
    CapabilityHealth,
    CapabilityPack,
    HealthStatus,
    StructuredFailure,
)
from kora_v2.capabilities.policy import PolicyMatrix, SessionState, TaskState
from kora_v2.capabilities.registry import ActionRegistry
from kora_v2.capabilities.vault.actions import (
    VaultActionContext,
    vault_read_note,
    vault_write_clip,
    vault_write_note,
)
from kora_v2.capabilities.vault.config import VaultCapabilityConfig, from_settings
from kora_v2.capabilities.vault.health import check_vault_health
from kora_v2.capabilities.vault.mirror import FilesystemMirror, MirrorTarget, NullMirror
from kora_v2.capabilities.vault.policy import build_vault_policy

if TYPE_CHECKING:
    from kora_v2.core.settings import Settings


# ── Action metadata table ─────────────────────────────────────────────────────
# (action_name, description, read_only, requires_approval)
_ACTION_METADATA: list[tuple[str, str, bool, bool]] = [
    ("vault.write_note", "Write a note to the vault filesystem mirror", False, False),
    ("vault.write_clip", "Write a web clip to the vault filesystem mirror", False, False),
    ("vault.read_note",  "Read a note from the vault filesystem mirror",  True,  False),
]

_ACTION_HANDLERS: dict[str, Any] = {
    "vault.write_note": vault_write_note,
    "vault.write_clip": vault_write_clip,
    "vault.read_note":  vault_read_note,
}

# ── JSON schemas for each action (keyed by full action name) ─────────────────
_VAULT_ACTION_SCHEMAS: dict[str, dict] = {
    "vault.write_note": {
        "type": "object",
        "properties": {
            "relative_path": {"type": "string", "description": "Relative path under the notes subdirectory"},
            "content": {"type": "string", "description": "Note content (text)"},
            "metadata": {"type": "object", "description": "Optional metadata key-value pairs"},
        },
        "required": ["relative_path", "content"],
    },
    "vault.write_clip": {
        "type": "object",
        "properties": {
            "source_url": {"type": "string", "description": "URL of the clipped page"},
            "title": {"type": "string", "description": "Title of the clip"},
            "content": {"type": "string", "description": "Clipped text content"},
            "metadata": {"type": "object", "description": "Optional metadata key-value pairs"},
        },
        "required": ["source_url", "title", "content"],
    },
    "vault.read_note": {
        "type": "object",
        "properties": {
            "relative_path": {"type": "string", "description": "Relative path under the notes subdirectory"},
        },
        "required": ["relative_path"],
    },
}


class VaultCapability(CapabilityPack):
    """Filesystem mirror target for clips, exports, and future vault organization."""

    name = "vault"
    description = "Filesystem mirror target for clips, exports, and future vault organization."

    def __init__(self, config: VaultCapabilityConfig | None = None) -> None:
        self._config = config
        self._policy: PolicyMatrix = build_vault_policy()
        self._target: MirrorTarget | None = None

    def bind(self, settings: Settings | None = None, **_kwargs: Any) -> None:
        """Late-bind runtime dependencies. Ignores mcp_manager."""
        if settings is None:
            return
        self._config = from_settings(settings)
        if self._config.enabled and self._config.root is not None:
            self._target = FilesystemMirror(
                root=self._config.root,
                clips_subdir=self._config.clips_subdir,
                notes_subdir=self._config.notes_subdir,
            )
        else:
            self._target = NullMirror()

    async def health_check(self) -> CapabilityHealth:
        if self._config is None:
            return CapabilityHealth(
                status=HealthStatus.UNCONFIGURED,
                summary="Vault capability not bound to runtime yet.",
                remediation=(
                    "Container must call .bind(settings) first, "
                    "or set KORA_VAULT__PATH in the environment."
                ),
            )
        return await check_vault_health(self._config)

    def register_actions(self, registry: ActionRegistry) -> None:
        """Register one Action per vault action into the registry."""
        cap_instance = self

        for full_name, description, read_only, requires_approval in _ACTION_METADATA:
            handler_fn = _ACTION_HANDLERS.get(full_name)

            def _make_handler(fn: Any, cap: VaultCapability, action_name: str) -> Any:
                async def _handler(
                    session: SessionState,
                    task: TaskState | None = None,
                    **call_kwargs: Any,
                ) -> Any:
                    if cap._target is None:
                        return StructuredFailure(
                            capability="vault",
                            action=action_name,
                            path="capability.unbound",
                            reason="capability_not_configured",
                            user_message=(
                                "The vault capability is not yet configured. "
                                "Run the daemon doctor to see remediation."
                            ),
                            recoverable=False,
                        )
                    ctx = cap.make_context(session=session, task=task)
                    return await fn(ctx, **call_kwargs)

                return _handler

            action = Action(
                name=full_name,
                description=description,
                capability=self.name,
                input_schema=_VAULT_ACTION_SCHEMAS.get(full_name, {"type": "object", "properties": {}}),
                requires_approval=requires_approval,
                read_only=read_only,
                handler=_make_handler(handler_fn, cap_instance, full_name) if handler_fn else None,
            )
            registry.register(action)

    def get_policy(self) -> PolicyMatrix:
        return self._policy

    def make_context(
        self,
        session: SessionState,
        task: TaskState | None = None,
    ) -> VaultActionContext:
        """Build a VaultActionContext for the current session/task."""
        if self._target is None:
            raise RuntimeError(
                "VaultCapability not bound — call .bind(settings) first."
            )
        if self._config is None:
            raise RuntimeError(
                "VaultCapability not bound — call .bind(settings) first."
            )
        return VaultActionContext(
            config=self._config,
            policy=self._policy,
            target=self._target,
            session=session,
            task=task,
        )
