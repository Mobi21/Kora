"""Provenance injection for workspace writes."""
from __future__ import annotations

import copy
from typing import Any

from kora_v2.capabilities.workspace.config import WorkspaceConfig


def inject_calendar_create_provenance(
    event_args: dict[str, Any],
    config: WorkspaceConfig,
) -> dict[str, Any]:
    """Return a copy of event_args with Kora provenance markers added.

    - Appends config.provenance_marker to description separated by two newlines
      (``\\n\\n``).  If description is absent, creates one.
    - Sets extendedProperties.private.{provenance_metadata_key} = provenance_metadata_value.
    - Leaves all other fields untouched.
    - Does NOT modify the input dict.
    """
    result = copy.deepcopy(event_args)

    # ── Description provenance ────────────────────────────────────────────
    existing_desc = result.get("description")
    if existing_desc:
        result["description"] = f"{existing_desc}\n\n{config.provenance_marker}"
    else:
        result["description"] = config.provenance_marker

    # ── Extended properties provenance ────────────────────────────────────
    extended = result.get("extendedProperties")
    if extended is None:
        extended = {}
    # Deep-copy inner dict to avoid aliasing if deepcopy missed nested structure
    private = dict(extended.get("private") or {})
    private[config.provenance_metadata_key] = config.provenance_metadata_value
    extended = dict(extended)  # ensure our own copy
    extended["private"] = private
    result["extendedProperties"] = extended

    return result
