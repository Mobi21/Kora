"""ADHD-aware neurodivergent support module (Phase 5).

This package provides the first implementation of the
``NeurodivergentModule`` protocol. Future modules for other conditions
(anxiety, depression, etc.) should register under the same protocol.

Exports
-------
* ``ADHDModule``      — the concrete module class
* ``ADHDProfile``     — the user's ADHD config (loaded from ``profile.yaml``)
* ``ADHDProfileLoader`` — reader/writer for the profile YAML
* ``NeurodivergentModule`` — the shared protocol
"""

from kora_v2.adhd.module import ADHDModule
from kora_v2.adhd.profile import (
    ADHDProfile,
    ADHDProfileLoader,
    MedicationScheduleEntry,
    MedicationWindow,
)
from kora_v2.adhd.protocol import (
    CheckInTrigger,
    EnergySignal,
    FocusState,
    NeurodivergentModule,
    OutputRule,
    PlanningConfig,
)

__all__ = [
    "ADHDModule",
    "ADHDProfile",
    "ADHDProfileLoader",
    "CheckInTrigger",
    "EnergySignal",
    "FocusState",
    "MedicationScheduleEntry",
    "MedicationWindow",
    "NeurodivergentModule",
    "OutputRule",
    "PlanningConfig",
]
