"""Life OS support profile package."""

from kora_v2.support.bootstrap import SupportBootstrapResult, SupportProfileBootstrapService
from kora_v2.support.profiles import (
    ProfileRuntimeConfig,
    SupportProfile,
    SupportProfileDefinition,
    SupportProfileSignal,
    ensure_support_profile_tables,
)
from kora_v2.support.registry import RuntimeSupportRules, SupportRegistry

__all__ = [
    "ProfileRuntimeConfig",
    "RuntimeSupportRules",
    "SupportBootstrapResult",
    "SupportProfile",
    "SupportProfileBootstrapService",
    "SupportProfileDefinition",
    "SupportProfileSignal",
    "SupportRegistry",
    "ensure_support_profile_tables",
]
