"""Session lifecycle management.

Manages: init pipeline (8 steps), end pipeline (5 steps),
HARD_STOP continuation, bridge notes, emotion decay.
"""
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from kora_v2.context.working_memory import WorkingMemoryLoader, estimate_energy
from kora_v2.core.events import EventType
from kora_v2.core.models import (
    EmotionalState,
    SessionBridge,
    SessionState,
)

log = structlog.get_logger(__name__)


def apply_emotion_decay(state: EmotionalState, hours_elapsed: float) -> EmotionalState:
    """Apply 20%/hr exponential decay toward neutral.

    Neutral target: valence=0, arousal=0.5, dominance=0.5
    Formula: new_value = neutral + (old_value - neutral) * (0.8 ^ hours_elapsed)

    Args:
        state: Previous emotional state.
        hours_elapsed: Hours since the state was recorded.

    Returns:
        New EmotionalState with decayed values and source="loaded".
    """
    if hours_elapsed <= 0:
        return state

    decay_factor = 0.8 ** hours_elapsed  # 20%/hr decay

    # Neutral targets
    neutral_valence = 0.0
    neutral_arousal = 0.5
    neutral_dominance = 0.5

    new_valence = neutral_valence + (state.valence - neutral_valence) * decay_factor
    new_arousal = neutral_arousal + (state.arousal - neutral_arousal) * decay_factor
    new_dominance = neutral_dominance + (state.dominance - neutral_dominance) * decay_factor

    # Clamp to valid ranges
    new_valence = max(-1.0, min(1.0, new_valence))
    new_arousal = max(0.0, min(1.0, new_arousal))
    new_dominance = max(0.0, min(1.0, new_dominance))

    from kora_v2.emotion.fast_assessor import _pad_to_mood
    new_mood = _pad_to_mood(new_valence, new_arousal, new_dominance)

    return EmotionalState(
        valence=round(new_valence, 3),
        arousal=round(new_arousal, 3),
        dominance=round(new_dominance, 3),
        mood_label=new_mood,
        confidence=state.confidence * decay_factor,  # Confidence decays too
        source="loaded",
    )


class SessionManager:
    """Manages session lifecycle: init → conversation → end.

    Session init pipeline (~1.5-3.5s):
    1. Generate session_id
    2. Load user context from memory store (soft enrichment)
    3. Build frozen prefix (with user knowledge)
    4. Load emotional state (with decay from last session)
    5. Run WorkingMemoryLoader
    6. Run energy_estimate()
    7. Write session start to operational.db
    8. Start greeting generation (happens at graph level, not here)

    Session end pipeline:
    1. Run signal scanner on conversation messages
    2. Save emotional state + session end record to operational.db
    3. Create session bridge note
    4. Emit session-end event
    """

    def __init__(self, container: Any):
        self.container = container
        self.active_session: SessionState | None = None
        self._thread_id: str | None = None

    def _data_dir_path(self) -> Path:
        """Return the data directory, defaulting to ``data/`` when unset."""
        settings = getattr(self.container, "settings", None)
        if settings is not None and hasattr(settings, "data_dir"):
            return Path(settings.data_dir)
        return Path("data")

    def _load_or_create_thread_id(self) -> str:
        """Load a persistent thread_id from disk, or create one.

        The thread_id is stored in ``data/thread_id`` so that the same
        LangGraph checkpoint thread is reused across daemon restarts,
        preserving conversation history when a durable checkpointer
        (SQLite) is active.
        """
        tid_path = self._data_dir_path() / "thread_id"

        # Try to load existing
        try:
            if tid_path.exists():
                stored = tid_path.read_text().strip()
                if stored:
                    return stored
        except OSError:
            pass

        # Generate new and persist
        new_id = f"kora-{uuid.uuid4().hex[:12]}"
        try:
            tid_path.parent.mkdir(parents=True, exist_ok=True)
            tid_path.write_text(new_id)
        except OSError:
            log.debug("thread_id_persist_failed")
        return new_id

    def _load_or_create_session_id(self) -> str:
        """Load a persistent session_id from disk, or create one.

        The session_id was previously regenerated on every init_session()
        call, which broke cross-restart memory lookups that were keyed on
        session_id (autonomous updates, projection DB entries, etc). We now
        persist it alongside thread_id so the same identity survives daemon
        restarts — until the user explicitly starts a new session via
        ``reset_session_id()``.
        """
        sid_path = self._data_dir_path() / "session_id"

        try:
            if sid_path.exists():
                stored = sid_path.read_text().strip()
                if stored:
                    return stored
        except OSError:
            pass

        new_id = uuid.uuid4().hex[:12]
        try:
            sid_path.parent.mkdir(parents=True, exist_ok=True)
            sid_path.write_text(new_id)
        except OSError:
            log.debug("session_id_persist_failed")
        return new_id

    def reset_thread_id(self) -> str:
        """Generate a new thread_id (e.g. for /new command). Returns the new ID."""
        tid_path = self._data_dir_path() / "thread_id"

        new_id = f"kora-{uuid.uuid4().hex[:12]}"
        try:
            tid_path.write_text(new_id)
        except OSError:
            pass
        self._thread_id = new_id
        return new_id

    def reset_session_id(self) -> str:
        """Generate a new session_id and persist it.

        Use for explicit "start fresh" flows. Does not affect thread_id.
        """
        sid_path = self._data_dir_path() / "session_id"
        new_id = uuid.uuid4().hex[:12]
        try:
            sid_path.parent.mkdir(parents=True, exist_ok=True)
            sid_path.write_text(new_id)
        except OSError:
            pass
        return new_id

    async def init_session(self) -> SessionState:
        """Run the session init pipeline."""
        # Step 1: Load persistent session_id + thread_id. Both survive
        # daemon restarts so that stored data keyed on them (checkpointer
        # state, autonomous updates, projection memories) still resolve
        # after a restart.
        session_id = self._load_or_create_session_id()
        self._thread_id = self._load_or_create_thread_id()

        # Step 2: Load user context from memory store (soft enrichment)
        memory_store = getattr(self.container, "memory_store", None)
        if memory_store and hasattr(memory_store, "list_notes"):
            try:
                notes = await memory_store.list_notes(layer="user_model", limit=5)
                if notes:
                    log.debug("user_context_loaded", count=len(notes))
            except Exception:
                log.debug("user_model_snapshot_unavailable")

        # Step 3: Frozen prefix built at graph level (not here)

        # Step 4: Load emotional state with decay
        last_bridge = await self.load_last_bridge()
        emotional_state = EmotionalState(
            valence=0.0, arousal=0.3, dominance=0.5,
            mood_label="neutral", confidence=0.5, source="loaded",
        )
        # If we had a saved emotional state, we'd load and decay it here

        # Step 5: WorkingMemoryLoader
        loader = WorkingMemoryLoader(
            projection_db=getattr(self.container, 'projection_db', None),
            items_db=getattr(self.container, 'db', None),  # operational DB for items table
            last_bridge=last_bridge,
        )
        pending_items = await loader.load()

        # Step 6: Energy estimate
        energy = estimate_energy()

        # Create session state
        self.active_session = SessionState(
            session_id=session_id,
            turn_count=0,
            started_at=datetime.now(UTC),
            emotional_state=emotional_state,
            energy_estimate=energy,
            pending_items=[item.model_dump() for item in pending_items],
        )

        # Emit SESSION_START event
        emitter = getattr(self.container, 'event_emitter', None)
        if emitter:
            await emitter.emit(
                EventType.SESSION_START,
                session_id=session_id,
            )

        # Write session record to operational.db.
        # INSERT OR IGNORE keeps the original started_at when the persisted
        # session_id already has a row (this happens after daemon restart
        # now that session_id is durable). The ended_at gets re-UPDATEd in
        # end_session() so it tracks the latest activity window.
        settings = getattr(self.container, 'settings', None)
        if settings and hasattr(settings, 'data_dir'):
            db_path = settings.data_dir / "operational.db"
            try:
                import aiosqlite

                async with aiosqlite.connect(str(db_path)) as db:
                    await db.execute(
                        "INSERT OR IGNORE INTO sessions "
                        "(id, started_at, emotional_state_start) VALUES (?,?,?)",
                        (session_id, datetime.now(UTC).isoformat(), emotional_state.model_dump_json()),
                    )
                    await db.commit()
            except Exception:
                log.debug("session_start_write_failed")

        log.info("session_initialized", session_id=session_id)
        return self.active_session

    async def end_session(
        self,
        messages: list[dict],
        emotional_state: EmotionalState,
    ) -> SessionBridge:
        """Run the session end pipeline. Returns bridge note."""
        session_id = self.active_session.session_id if self.active_session else "unknown"

        # Step 1: Run signal scanner on conversation messages
        scanner = getattr(self.container, "signal_scanner", None)
        if scanner and hasattr(scanner, "scan"):
            try:
                await scanner.scan(messages)
            except Exception:
                log.debug("signal_scanner_failed_at_session_end")

        # Step 2: Save emotional state + session end record
        settings = getattr(self.container, 'settings', None)
        if settings and hasattr(settings, 'data_dir'):
            db_path = settings.data_dir / "operational.db"
            try:
                import aiosqlite

                duration = int((datetime.now(UTC) - self.active_session.started_at).total_seconds()) if self.active_session else 0
                turn_count = self.active_session.turn_count if self.active_session else 0
                async with aiosqlite.connect(str(db_path)) as db:
                    await db.execute(
                        """UPDATE sessions SET ended_at=?, turn_count=?, duration_seconds=?,
                           emotional_state_end=?, bridge_note_path=? WHERE id=?""",
                        (datetime.now(UTC).isoformat(), turn_count, duration,
                         emotional_state.model_dump_json(), None, session_id),
                    )
                    await db.commit()
            except Exception:
                log.debug("session_end_write_failed")

        # Step 3: Create bridge note
        summary = self._summarize_messages(messages)
        open_threads = self._extract_open_threads(messages)

        bridge = SessionBridge(
            session_id=session_id,
            summary=summary,
            open_threads=open_threads,
            emotional_trajectory=f"Session ended with mood: {emotional_state.mood_label}",
        )

        # Step 4: Emit SESSION_END event
        emitter = getattr(self.container, 'event_emitter', None)
        if emitter:
            await emitter.emit(
                EventType.SESSION_END,
                session_id=session_id,
            )
        # Save bridge note to filesystem
        await self._save_bridge(bridge)

        # Clear active session
        self.active_session = None
        self._thread_id = None

        log.info("session_ended", session_id=session_id)
        return bridge

    async def handle_hard_stop(
        self,
        messages: list[dict],
        state: dict,
    ) -> SessionBridge:
        """Handle HARD_STOP: create bridge with compressed context."""
        from kora_v2.context.compaction import build_hard_stop_bridge
        session_id = state.get("session_id", "unknown")
        bridge = build_hard_stop_bridge(messages, session_id)
        await self._save_bridge(bridge)
        self.active_session = None
        self._thread_id = None
        return bridge

    async def load_last_bridge(self) -> SessionBridge | None:
        """Load the most recent bridge note from filesystem."""
        bridges_dir = self._bridges_dir()
        if not bridges_dir.exists():
            return None

        # Find most recent bridge file
        bridge_files = sorted(bridges_dir.glob("*.md"), reverse=True)
        if not bridge_files:
            return None

        try:
            content = bridge_files[0].read_text()
            # Parse simple format: first line = session_id, rest = summary
            lines = content.strip().split("\n")
            session_id = lines[0].replace("# Session: ", "").strip() if lines else "unknown"
            summary = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

            return SessionBridge(
                session_id=session_id,
                summary=summary,
            )
        except Exception:
            log.warning("failed_to_load_bridge", path=str(bridge_files[0]))
            return None

    async def generate_greeting(self, graph: Any, config: dict) -> str:
        """Generate a context-dependent greeting via the supervisor graph.

        If there is a bridge from the last session with open threads the
        greeting references the most relevant thread.  Otherwise a brief
        generic greeting is produced.

        Args:
            graph: Compiled supervisor LangGraph graph.
            config: Graph config dict (must contain ``configurable.thread_id``).

        Returns:
            Greeting text string.
        """
        if not self.active_session:
            return "Hey! What's on your mind?"

        # Build a greeting prompt based on context
        bridge = await self.load_last_bridge()
        if bridge and bridge.open_threads:
            greeting_prompt = (
                f"Generate a brief, warm greeting for the user. "
                f"Last session summary: {bridge.summary}. "
                f"Open threads: {', '.join(bridge.open_threads[:3])}. "
                f"Mention the most relevant open thread naturally."
            )
        else:
            greeting_prompt = "Generate a brief, warm greeting. Nothing specific pending."

        # Use a separate thread for greeting so it doesn't pollute the
        # main conversation checkpoint with greeting prompt messages.
        greeting_config = {
            "configurable": {"thread_id": f"greeting-{self.active_session.session_id}"},
        }
        try:
            result = await graph.ainvoke(
                {
                    "messages": [{"role": "system", "content": greeting_prompt}],
                    "greeting_sent": True,
                },
                greeting_config,
            )
            return result.get("response_content") or "Hey! What's up?"
        except Exception:
            log.warning("greeting_generation_failed")
            return "Hey! What's on your mind?"

    def get_restart_context(self) -> str:
        """Build grounded context snippet for restart paths.

        Returns a short string summarizing what we know about the current
        session state to prevent contradictory memory (greeting the user
        as if new when we have bridge data, or vice versa).
        """
        if not self.active_session:
            return ""

        parts = []
        session = self.active_session

        # Session continuity signal
        if session.pending_items:
            threads = [item.get("content", "") for item in session.pending_items[:3]]
            valid_threads = [t for t in threads if t]
            if valid_threads:
                parts.append(f"Open from last session: {'; '.join(valid_threads)}")

        if parts:
            return "Session context: " + " | ".join(parts)
        return ""

    def get_thread_id(self) -> str:
        """Return persistent thread_id for this session."""
        if self._thread_id is None:
            self._thread_id = f"session-{uuid.uuid4().hex[:12]}"
        return self._thread_id

    def _bridges_dir(self) -> Path:
        """Get the bridges directory path."""
        data_dir = getattr(self.container, 'settings', None)
        if data_dir and hasattr(data_dir, 'memory') and hasattr(data_dir.memory, 'kora_memory_path'):
            base = Path(data_dir.memory.kora_memory_path)
        else:
            base = Path("_KoraMemory")
        return base / ".kora" / "bridges"

    async def _save_bridge(self, bridge: SessionBridge) -> None:
        """Save bridge note to filesystem."""
        bridges_dir = self._bridges_dir()
        try:
            bridges_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{bridge.created_at.strftime('%Y%m%d_%H%M%S')}_{bridge.session_id}.md"
            filepath = bridges_dir / filename
            content = f"# Session: {bridge.session_id}\n{bridge.summary}\n"
            if bridge.open_threads:
                content += "\n## Open Threads\n"
                for thread in bridge.open_threads:
                    content += f"- {thread}\n"
            filepath.write_text(content)
            log.debug("bridge_saved", path=str(filepath))
        except Exception:
            log.warning("failed_to_save_bridge", session_id=bridge.session_id)

    def _summarize_messages(self, messages: list[dict]) -> str:
        """Build a structured bridge summary from messages.

        Always includes a Topics line (from user messages).  Key points
        from assistant responses are appended only when available.  Each
        topic is truncated to 80 chars and the total summary is capped at
        500 chars to keep bridge notes compact.
        """
        user_msgs: list[str] = []
        assistant_msgs: list[str] = []
        for m in messages:
            role = m.get("role", "") if isinstance(m, dict) else getattr(m, "type", "")
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            if not isinstance(content, str):
                continue
            if role in ("user", "human"):
                user_msgs.append(content)
            elif role in ("assistant", "ai"):
                assistant_msgs.append(content)

        if not user_msgs:
            return "Empty session"

        def _truncate(text: str, limit: int = 80) -> str:
            text = text.strip()
            if len(text) <= limit:
                return text
            return text[:limit - 3].rstrip() + "..."

        # Build summary
        parts: list[str] = []

        # Main topics from user messages (always present)
        topics = [_truncate(msg) for msg in user_msgs[-5:] if msg.strip()]
        parts.append("Topics: " + "; ".join(topics) if topics else "Topics: (none)")

        # Key points from assistant (first sentence of last 3 responses)
        if assistant_msgs:
            key_points: list[str] = []
            for msg in assistant_msgs[-3:]:
                first_sentence = msg.split(".")[0].split("!")[0].split("?")[0]
                if first_sentence and len(first_sentence.strip()) > 10:
                    key_points.append(_truncate(first_sentence.strip(), 100))
            if key_points:
                parts.append("Key points: " + "; ".join(key_points))

        summary = "\n".join(parts)
        if len(summary) > 500:
            summary = summary[:497] + "..."
        return summary

    def _extract_open_threads(self, messages: list[dict]) -> list[str]:
        """Extract questions and unresolved topics from recent messages."""
        threads = []
        recent = messages[-10:] if len(messages) > 10 else messages
        for msg in recent:
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if isinstance(content, str) and content.strip().endswith("?"):
                threads.append(content.strip()[:150])
        return threads[:5]
