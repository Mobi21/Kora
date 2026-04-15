"""Working document primitive — stub for slice 7.5a.

In slice 7.5b the working doc becomes a bidirectional filesystem
contract that pipelines read and write so humans and Kora can share a
mutable workspace per pipeline run. Slice 7.5a only needs a placeholder
type so the engine can store a ``working_doc_path`` on
:class:`~kora_v2.runtime.orchestration.pipeline.PipelineInstance`
without pulling in the real file-watcher implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorkingDocHandle:
    """Lightweight reference to a working-doc file on disk."""

    path: Path
    mtime: float = 0.0

    def exists(self) -> bool:
        return self.path.exists()
