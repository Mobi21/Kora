"""Kora V2 — Typed dependency injection container.

Central registry of services and infrastructure that nodes, agents, and
the daemon share.

Phase 1: LLM, event bus, supervisor graph.
Phase 2: Memory subsystem (embedding model, projection DB, store, pipeline).
Phase 3: Workers (planner, executor, reviewer), MCP, skills, verb resolver.
Phase 4: Emotion, quality, session manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from kora_v2.core.events import EventEmitter
from kora_v2.core.settings import Settings
from kora_v2.llm.minimax import MiniMaxProvider

if TYPE_CHECKING:
    from kora_v2.agents.workers.executor import ExecutorWorkerHarness
    from kora_v2.agents.workers.planner import PlannerWorkerHarness
    from kora_v2.agents.workers.reviewer import ReviewerWorkerHarness
    from kora_v2.mcp.manager import MCPManager
    from kora_v2.skills.loader import SkillLoader
    from kora_v2.tools.verb_resolver import DomainVerbResolver

log = structlog.get_logger(__name__)


class Container:
    """Typed dependency injection container.

    Phase 1: LLM provider, event emitter, supervisor graph.
    Phase 2: Embedding model, projection DB, filesystem store, write pipeline.
    Phase 3: Workers, MCP manager, skill loader, verb resolver.
    Phase 4: Emotion assessors, quality collector, session manager.

    Usage::

        from kora_v2.core.settings import get_settings
        from kora_v2.core.di import Container

        container = Container(get_settings())
        graph = container.supervisor_graph  # lazy-build
        await container.initialize_memory()  # Phase 2+
        container.initialize_workers()  # Phase 3+
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # Infrastructure
        self.llm = MiniMaxProvider(settings.llm)
        self.event_emitter = EventEmitter()

        # Graph (built lazily on first access)
        self._supervisor_graph: Any | None = None

        # Memory subsystem (initialized asynchronously via initialize_memory)
        self.embedding_model: Any | None = None
        self.projection_db: Any | None = None
        self.memory_store: Any | None = None
        self.write_pipeline: Any | None = None
        self.signal_scanner: Any | None = None

        # Phase 3: Workers + tool infrastructure (initialized via initialize_workers)
        self._planner: PlannerWorkerHarness | None = None
        self._executor: ExecutorWorkerHarness | None = None
        self._reviewer: ReviewerWorkerHarness | None = None
        self._mcp_manager: MCPManager | None = None
        self._skill_loader: SkillLoader | None = None
        self._verb_resolver: DomainVerbResolver | None = None

        # Phase 4: Conversation quality (initialized via initialize_phase4)
        self.fast_emotion: Any | None = None
        self.llm_emotion: Any | None = None
        self.quality_collector: Any | None = None
        self.session_manager: Any | None = None

        # Phase 4.67: SQLite checkpointer for LangGraph state persistence.
        # Set by initialize_checkpointer() (async); None = use MemorySaver.
        self._checkpointer: Any | None = None

        # Phase 6B: Routine manager for guided routines.
        self._routine_manager: Any | None = None
        self._reminder_store: Any | None = None

        # Life OS pivot services (lazy-built via properties).
        self._domain_event_store: Any | None = None
        self._life_event_ledger: Any | None = None
        self._day_plan_service: Any | None = None
        self._support_registry: Any | None = None
        self._support_profile_bootstrap: Any | None = None
        self._crisis_safety_router: Any | None = None
        self._life_load_engine: Any | None = None
        self._day_repair_engine: Any | None = None
        self._proactivity_policy_engine: Any | None = None
        self._stabilization_mode_service: Any | None = None
        self._context_pack_service: Any | None = None
        self._future_self_bridge_service: Any | None = None
        self._trusted_support_export_service: Any | None = None
        self._social_sensory_support_service: Any | None = None

        # Phase 5: ADHD life engine components (lazy-built via properties).
        self._adhd_profile: Any | None = None
        self._adhd_module: Any | None = None
        self._context_engine: Any | None = None
        self._calendar_sync: Any | None = None

        # Auth relay (set by RuntimeKernel)
        self._auth_relay: Any | None = None

        # Phase 7.5: Orchestration engine (lazy async init).
        #
        # Slice 7.5c §17.7c retired the legacy ``_autonomous_loops``
        # dict that used to hold ``asyncio.Task`` handles for
        # ``AutonomousExecutionLoop`` instances. Autonomous work now
        # runs as a ``user_autonomous_task`` pipeline on the
        # orchestration engine, and shutdown cancellation is handled
        # by ``self._orchestration_engine.stop(graceful=True)`` below.
        self._orchestration_engine: Any | None = None

        log.info(
            "container_initialized",
            llm_model=settings.llm.model,
        )

    # ── Checkpointer Initialization (Phase 4.67) ─────────────────

    async def initialize_checkpointer(self) -> None:
        """Initialize the SQLite-backed LangGraph checkpointer.

        Creates the checkpointer, loads any persisted checkpoints, and
        stores it on ``self._checkpointer`` so that ``supervisor_graph``
        picks it up on next lazy-build.

        If the graph was already built (with MemorySaver), resets it so
        the next access rebuilds with the SQLite checkpointer.

        Should be called once at daemon startup, before the first
        supervisor_graph access.
        """
        from kora_v2.runtime.checkpointer import make_checkpointer

        db_path = self.settings.data_dir / "operational.db"
        self._checkpointer = await make_checkpointer(db_path)

        # If graph was already built, reset so next access rebuilds
        if self._supervisor_graph is not None:
            log.warning(
                "supervisor_graph_rebuilt_with_sqlite_checkpointer",
                reason="graph was built before checkpointer was ready",
            )
            self._supervisor_graph = None

        log.info("sqlite_checkpointer_initialized", db_path=str(db_path))

    # ── Memory Initialization (Phase 2) ──────────────────────────

    async def initialize_memory(self) -> None:
        """Initialize the memory subsystem asynchronously.

        Loads the embedding model, opens projection DB (with sqlite-vec
        and migrations), creates the filesystem store and write pipeline.

        Should be called once at daemon startup after the container
        is constructed.
        """
        from kora_v2.memory.embeddings import LocalEmbeddingModel
        from kora_v2.memory.projection import ProjectionDB
        from kora_v2.memory.signal_scanner import SignalScanner
        from kora_v2.memory.store import FilesystemMemoryStore
        from kora_v2.memory.write_pipeline import WritePipeline

        # Embedding model (lazy-load on first use, but we can init the wrapper)
        self.embedding_model = LocalEmbeddingModel(self.settings.memory)
        log.info("embedding_model_wrapper_created")

        # Projection DB
        db_path = self.settings.data_dir / "projection.db"
        self.projection_db = await ProjectionDB.initialize(db_path)
        log.info("projection_db_initialized", path=str(db_path))

        # Filesystem memory store
        memory_path = Path(self.settings.memory.kora_memory_path)
        self.memory_store = FilesystemMemoryStore(memory_path)
        log.info("memory_store_initialized", path=str(memory_path))

        # Write pipeline
        self.write_pipeline = WritePipeline(
            store=self.memory_store,
            projection_db=self.projection_db,
            embedding_model=self.embedding_model,
            llm=None,  # LLM for dedup wired when needed
            event_emitter=self.event_emitter,
        )
        log.info("write_pipeline_initialized")

        # Signal scanner
        self.signal_scanner = SignalScanner()
        log.info("signal_scanner_initialized")

    # ── Embedding Service Alias (Phase 6) ─────────────────────────

    @property
    def embedding_service(self) -> Any | None:
        """Alias for embedding_model — used by autonomous overlap detection."""
        return self.embedding_model

    # ── Supervisor Graph ──────────────────────────────────────────

    @property
    def supervisor_graph(self) -> Any:
        """Lazy-build the supervisor graph.

        Returns the compiled LangGraph StateGraph with MemorySaver
        checkpointer.  First access triggers the build; subsequent
        accesses return the cached instance.
        """
        if self._supervisor_graph is None:
            from kora_v2.graph.supervisor import build_supervisor_graph

            self._supervisor_graph = build_supervisor_graph(self)
            log.info("supervisor_graph_built_via_container")
        return self._supervisor_graph

    # ── Worker Initialization (Phase 3) ──────────────────────────

    def initialize_workers(self) -> None:
        """Initialize the worker agents and tool infrastructure.

        Creates: skill loader, the three core worker harnesses
        (planner, executor, reviewer), and the MCP manager.

        The MCP manager itself does not spawn subprocesses here —
        subprocess startup happens either lazily on first tool call or
        eagerly via :meth:`initialize_mcp` (called by the daemon).

        Should be called once at daemon startup after the container
        is constructed (can be called before or after initialize_memory).
        """
        # Ensure tool modules are imported so @tool decorators register them
        import kora_v2.tools.calendar  # noqa: F401
        import kora_v2.tools.filesystem  # noqa: F401
        import kora_v2.tools.life_management  # noqa: F401
        import kora_v2.tools.life_os  # noqa: F401
        import kora_v2.tools.planning  # noqa: F401
        import kora_v2.tools.routines  # noqa: F401
        from kora_v2.agents.workers.executor import ExecutorWorkerHarness
        from kora_v2.agents.workers.planner import PlannerWorkerHarness
        from kora_v2.agents.workers.reviewer import ReviewerWorkerHarness
        from kora_v2.mcp.manager import MCPManager
        from kora_v2.skills.loader import SkillLoader

        # Skill Loader (loads all YAML files on init)
        self._skill_loader = SkillLoader()
        self._skill_loader.load_all()
        skill_count = len(self._skill_loader.get_all_skills())
        log.info(
            "skill_loader_initialized",
            skill_count=skill_count,
        )
        # Loud failure: if zero skills loaded, downstream tool gating
        # will collapse the LLM's visible toolset to just the supervisor
        # tools, and any tool it thinks it's calling will hallucinate.
        # This has been the turn-1 "Logged your Adderall" deception path.
        if skill_count == 0:
            from kora_v2.skills.loader import _DEFAULT_SKILLS_DIR
            log.error(
                "skill_loader_empty",
                skills_dir=str(_DEFAULT_SKILLS_DIR),
                hint="Check YAML parse errors in the skills directory.",
            )

        # Core workers (singletons — reused across turns)
        self._planner = PlannerWorkerHarness(self)
        self._executor = ExecutorWorkerHarness(self)
        self._reviewer = ReviewerWorkerHarness(self)
        log.info("core_workers_initialized")

        # MCP manager — lazy subprocess start; failures do not crash the daemon.
        self._mcp_manager = MCPManager(self.settings.mcp)
        log.info(
            "mcp_manager_initialized",
            server_count=len(self.settings.mcp.servers),
        )

        # Verb resolver — maps natural language verbs to tool names
        from kora_v2.tools.verb_resolver import DomainVerbResolver

        self._verb_resolver = DomainVerbResolver()
        log.info("verb_resolver_initialized")

        # Capability pack binding — late-bind settings + mcp_manager into any
        # capability pack that exposes a .bind() method.  This keeps capability
        # packs free of constructor DI while still receiving runtime deps.
        self._bind_capabilities()

    def _bind_capabilities(self) -> None:
        """Late-bind settings and mcp_manager into capability packs that support it.

        Iterates all registered capability packs and calls ``pack.bind(settings,
        mcp_manager)`` for any pack that exposes that method.  Missing method or
        any exception is logged at debug level and never crashes the daemon.
        """
        try:
            import inspect

            from kora_v2.capabilities.registry import get_all_capabilities
            for pack in get_all_capabilities():
                bind_fn = getattr(pack, "bind", None)
                if callable(bind_fn):
                    try:
                        # Prefer keyword-argument style so packs can accept
                        # only the deps they need via **kwargs.
                        sig = inspect.signature(bind_fn)
                        params = sig.parameters
                        has_var_keyword = any(
                            p.kind == inspect.Parameter.VAR_KEYWORD
                            for p in params.values()
                        )
                        if has_var_keyword:
                            # Pack accepts **kwargs — pass everything by name.
                            bind_fn(
                                settings=self.settings,
                                mcp_manager=self._mcp_manager,
                            )
                        else:
                            # Legacy packs accept (settings, mcp_manager) positionally.
                            bind_fn(self.settings, self._mcp_manager)
                        log.debug(
                            "capability_bound",
                            pack=pack.name,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "capability_bind_failed",
                            pack=pack.name,
                            error=str(exc),
                        )
        except Exception as exc:  # noqa: BLE001
            log.debug("capability_binding_skipped", error=str(exc))

    async def initialize_mcp(self) -> None:
        """Start every configured MCP server (best-effort).

        Failures for individual servers are logged as warnings and do
        not crash the daemon. Servers that fail here end up in the
        ``FAILED`` state; ``call_tool`` will surface a clear error.
        """
        if self._mcp_manager is None:
            return
        for name in self.settings.mcp.servers:
            try:
                await self._mcp_manager.ensure_server_running(name)
                log.info("mcp_server_started", server=name)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "mcp_server_start_failed",
                    server=name,
                    error=str(exc),
                )

    # ── Phase 4 Initialization ─────────────────────────────────────

    def initialize_phase4(self) -> None:
        """Initialize Phase 4 services: emotion, quality, session manager.

        Creates: fast emotion assessor, LLM emotion assessor,
        quality metric collector, and the session manager.

        Should be called once at daemon startup after the container
        is constructed (can be called before or after initialize_memory
        or initialize_workers).
        """
        from kora_v2.daemon.session import SessionManager
        from kora_v2.emotion.fast_assessor import FastEmotionAssessor
        from kora_v2.emotion.llm_assessor import LLMEmotionAssessor
        from kora_v2.quality.tier1 import QualityCollector

        self.fast_emotion = FastEmotionAssessor()
        self.llm_emotion = LLMEmotionAssessor(
            self.llm,
            event_emitter=self.event_emitter,
        )
        self.quality_collector = QualityCollector(
            db_path=self.settings.data_dir / "operational.db"
        )
        self.session_manager = SessionManager(self)

        # Phase 6B: Routine manager
        from kora_v2.life.reminders import ReminderStore
        from kora_v2.life.routines import RoutineManager

        operational_db = self.settings.data_dir / "operational.db"
        self._routine_manager = RoutineManager(operational_db)
        self._reminder_store = ReminderStore(operational_db)

        log.info("phase4_services_initialized")

    # ── Worker Resolution (Phase 3) ──────────────────────────────

    def resolve_worker(self, name: str) -> Any:
        """Resolve a worker agent by name.

        Core workers (planner, executor, reviewer) are singletons.

        Args:
            name: Worker identifier (e.g. ``"planner"``).

        Returns:
            Configured AgentHarness for the requested worker.

        Raises:
            ValueError: If the worker name is unknown.
            RuntimeError: If workers have not been initialized yet.
        """
        _CORE_NAMES = {"planner", "executor", "reviewer"}

        if name in _CORE_NAMES:
            core_workers = {
                "planner": self._planner,
                "executor": self._executor,
                "reviewer": self._reviewer,
            }
            worker = core_workers[name]
            if worker is None:
                raise RuntimeError(
                    f"Worker '{name}' not initialized. "
                    "Call container.initialize_workers() first."
                )
            return worker

        raise ValueError(
            f"Unknown worker: '{name}'. Available workers: planner, executor, reviewer."
        )

    # ── Property Accessors (Phase 3) ─────────────────────────────

    @property
    def mcp_manager(self) -> MCPManager | None:
        """MCP server lifecycle manager."""
        return self._mcp_manager

    @property
    def skill_loader(self) -> SkillLoader | None:
        """Skill YAML loader."""
        return self._skill_loader

    @property
    def verb_resolver(self) -> DomainVerbResolver | None:
        """Domain verb → tool resolver."""
        return self._verb_resolver

    @property
    def routine_manager(self) -> Any:
        """RoutineManager for guided routine sessions."""
        return self._routine_manager

    @property
    def reminder_store(self) -> Any:
        """ReminderStore for continuity checks and scheduled nudges."""
        return self._reminder_store

    # ── Phase 5: ADHD life engine ─────────────────────────────────

    @property
    def adhd_profile(self) -> Any:
        """Lazy-loaded ``ADHDProfile`` from ``_KoraMemory/User Model/``.

        Reads ``profile.yaml`` on first access. Returns a default
        profile (empty schedule, default time_correction_factor=1.5)
        when the file does not yet exist — the user can populate it
        through the first-run wizard or by hand-editing the YAML.
        """
        if self._adhd_profile is None:
            from kora_v2.adhd.profile import ADHDProfileLoader

            base = Path(self.settings.memory.kora_memory_path)
            loader = ADHDProfileLoader(base)
            try:
                self._adhd_profile = loader.load()
            except Exception:
                log.debug("adhd_profile_load_failed", exc_info=True)
                from kora_v2.adhd.profile import ADHDProfile

                self._adhd_profile = ADHDProfile()
        return self._adhd_profile

    @property
    def adhd_module(self) -> Any:
        """Lazy-built ``ADHDModule`` wired with the live ``ADHDProfile``."""
        if self._adhd_module is None:
            from kora_v2.adhd.module import ADHDModule

            self._adhd_module = ADHDModule(self.adhd_profile)
        return self._adhd_module

    @property
    def context_engine(self) -> Any:
        """Lazy-built ``ContextEngine`` reading from operational.db."""
        if self._context_engine is None:
            from kora_v2.context.engine import ContextEngine

            db_path = self.settings.data_dir / "operational.db"
            self._context_engine = ContextEngine(
                db_path,
                self.adhd_module,
                user_tz_name=self.settings.user_tz,
            )
        return self._context_engine

    @property
    def calendar_sync(self) -> Any:
        """Lazy-built ``CalendarSync`` — thin wrapper over the Google
        Calendar MCP server (best-effort; None return paths are fine)."""
        if self._calendar_sync is None:
            from kora_v2.tools.calendar import CalendarSync

            self._calendar_sync = CalendarSync(self)
        return self._calendar_sync

    # ── Life OS pivot services ──────────────────────────────────

    @property
    def operational_db_path(self) -> Path:
        return self.settings.data_dir / "operational.db"

    @property
    def domain_event_store(self) -> Any:
        if self._domain_event_store is None:
            from kora_v2.life.domain_events import DomainEventStore

            self._domain_event_store = DomainEventStore(self.operational_db_path)
        return self._domain_event_store

    @property
    def life_event_ledger(self) -> Any:
        if self._life_event_ledger is None:
            from kora_v2.life.ledger import LifeEventLedger

            self._life_event_ledger = LifeEventLedger(
                self.operational_db_path,
                domain_events=self.domain_event_store,
            )
        return self._life_event_ledger

    @property
    def day_plan_service(self) -> Any:
        if self._day_plan_service is None:
            from kora_v2.life.day_plan import DayPlanService

            self._day_plan_service = DayPlanService(
                self.operational_db_path,
                ledger=self.life_event_ledger,
                domain_events=self.domain_event_store,
            )
        return self._day_plan_service

    @property
    def support_registry(self) -> Any:
        if self._support_registry is None:
            from kora_v2.support.registry import SupportRegistry

            self._support_registry = SupportRegistry(self.operational_db_path)
        return self._support_registry

    @property
    def support_profile_bootstrap(self) -> Any:
        if self._support_profile_bootstrap is None:
            from kora_v2.support.bootstrap import SupportProfileBootstrapService

            self._support_profile_bootstrap = SupportProfileBootstrapService(
                self.operational_db_path,
                registry=self.support_registry,
            )
        return self._support_profile_bootstrap

    @property
    def crisis_safety_router(self) -> Any:
        if self._crisis_safety_router is None:
            from kora_v2.safety.crisis import CrisisSafetyRouter

            self._crisis_safety_router = CrisisSafetyRouter(self.operational_db_path)
        return self._crisis_safety_router

    @property
    def life_load_engine(self) -> Any:
        if self._life_load_engine is None:
            from kora_v2.life.load import LifeLoadEngine

            self._life_load_engine = LifeLoadEngine(
                self.operational_db_path,
                support_registry=self.support_registry,
            )
        return self._life_load_engine

    @property
    def day_repair_engine(self) -> Any:
        if self._day_repair_engine is None:
            from kora_v2.life.repair import DayRepairEngine

            self._day_repair_engine = DayRepairEngine(self.operational_db_path)
        return self._day_repair_engine

    @property
    def proactivity_policy_engine(self) -> Any:
        if self._proactivity_policy_engine is None:
            from kora_v2.life.proactivity_policy import ProactivityPolicyEngine

            self._proactivity_policy_engine = ProactivityPolicyEngine(
                self.operational_db_path
            )
        return self._proactivity_policy_engine

    @property
    def stabilization_mode_service(self) -> Any:
        if self._stabilization_mode_service is None:
            from kora_v2.life.stabilization import StabilizationModeService

            self._stabilization_mode_service = StabilizationModeService(
                self.operational_db_path,
                day_plan_service=self.day_plan_service,
            )
        return self._stabilization_mode_service

    @property
    def context_pack_service(self) -> Any:
        if self._context_pack_service is None:
            from kora_v2.life.context_packs import ContextPackService

            self._context_pack_service = ContextPackService(
                self.operational_db_path,
                Path(self.settings.memory.kora_memory_path).expanduser(),
            )
        return self._context_pack_service

    @property
    def future_self_bridge_service(self) -> Any:
        if self._future_self_bridge_service is None:
            from kora_v2.life.future_bridge import FutureSelfBridgeService

            self._future_self_bridge_service = FutureSelfBridgeService(
                self.operational_db_path,
                Path(self.settings.memory.kora_memory_path).expanduser(),
                day_plan_service=self.day_plan_service,
            )
        return self._future_self_bridge_service

    @property
    def trusted_support_export_service(self) -> Any:
        if self._trusted_support_export_service is None:
            from kora_v2.life.trusted_support import TrustedSupportExportService

            self._trusted_support_export_service = TrustedSupportExportService(
                self.operational_db_path
            )
        return self._trusted_support_export_service

    @property
    def social_sensory_support_service(self) -> Any:
        if self._social_sensory_support_service is None:
            from kora_v2.life.trusted_support import SocialSensorySupportService

            self._social_sensory_support_service = SocialSensorySupportService(
                self.operational_db_path
            )
        return self._social_sensory_support_service

    # ── Phase 7.5: Orchestration engine ──────────────────────────

    @property
    def orchestration_engine(self) -> Any | None:
        """The :class:`OrchestrationEngine` instance (set by init)."""
        return self._orchestration_engine

    @property
    def notification_gate(self) -> Any | None:
        """The orchestration :class:`NotificationGate`, if the engine is up.

        Proactive handlers call ``container.notification_gate`` to send
        templated nudges. Exposing it here keeps callers from having to
        reach through ``container.orchestration_engine.notifications`` and
        gives the container a single no-op surface when orchestration is
        not yet initialized (returns ``None``).
        """
        engine = self._orchestration_engine
        if engine is None:
            return None
        return getattr(engine, "notifications", None)

    async def initialize_orchestration(
        self,
        *,
        websocket_broadcast: Any | None = None,
        session_active_fn: Any | None = None,
        hyperfocus_active_fn: Any | None = None,
    ) -> Any:
        """Create and return the :class:`OrchestrationEngine`.

        This is an async init step because the engine applies SQL
        migrations (``init_orchestration_schema``) on first ``start()``.
        The engine itself is cheap to construct — ``start()`` is the
        expensive call, and the daemon owns when that happens.

        Args:
            websocket_broadcast: Optional async callable used by the
                notification gate to push messages to connected CLI
                clients. The daemon passes ``_broadcast_to_clients``.
            session_active_fn: Optional predicate returning True when
                at least one CLI client is connected — used by the gate
                to decide whether ``send_templated`` requests should
                actually deliver or wait until a session is live.
            hyperfocus_active_fn: Optional predicate returning True when
                the user is in a hyperfocus block — used to suppress
                notifications per the profile's
                ``hyperfocus_suppression`` flag.

        Returns:
            The constructed :class:`OrchestrationEngine` — also cached
            on ``self._orchestration_engine``.
        """
        from kora_v2.runtime.orchestration.engine import OrchestrationEngine
        from kora_v2.runtime.orchestration.system_state import (
            UserScheduleProfile,
        )

        db_path = self.settings.data_dir / "operational.db"
        memory_root = Path(self.settings.memory.kora_memory_path)

        # Build a best-effort schedule profile from the ADHD profile.
        # Any missing/typed-wrong field falls back to the default
        # ``UserScheduleProfile()`` so engine construction never crashes
        # when the user hasn't finished the first-run wizard.
        schedule_profile = UserScheduleProfile()
        try:
            profile = self.adhd_profile
            if profile is not None:
                kwargs: dict[str, Any] = {}
                for field_name in (
                    "wake_time",
                    "sleep_start",
                    "sleep_end",
                    "dnd_start",
                    "dnd_end",
                    "timezone",
                    "weekly_review_time",
                    "weekly_review_weekday",
                    "hyperfocus_suppression",
                ):
                    value = getattr(profile, field_name, None)
                    if value is not None:
                        kwargs[field_name] = value
                if kwargs:
                    schedule_profile = UserScheduleProfile(**kwargs)
        except Exception:
            log.debug("schedule_profile_build_failed", exc_info=True)

        engine = OrchestrationEngine(
            db_path=db_path,
            event_emitter=self.event_emitter,
            schedule_profile=schedule_profile,
            memory_root=memory_root,
            websocket_broadcast=websocket_broadcast,
            session_active_fn=session_active_fn,
            hyperfocus_active_fn=hyperfocus_active_fn,
            container=self,
        )
        self._orchestration_engine = engine
        log.info(
            "orchestration_engine_constructed",
            db_path=str(db_path),
            memory_root=str(memory_root),
        )
        return engine

    # ── Cleanup ───────────────────────────────────────────────────

    async def close(self) -> None:
        """Release resources held by the container.

        Order matters: close the LangGraph checkpointer *first* so any
        pending checkpoint writes are flushed to SQLite before the
        enclosing connection is torn down. Then projection DB, then the
        embedding model (unloaded last because it has no durable state).
        """
        # 0. Stop the orchestration engine so the dispatcher, trigger
        #     scheduler, and notification gate get a clean shutdown
        #     before anything else tears down. A graceful stop allows
        #     in-flight tasks to complete without being cancelled.
        if self._orchestration_engine is not None:
            try:
                await self._orchestration_engine.stop(graceful=True)
                log.info("orchestration_engine_stopped_via_container")
            except Exception:
                log.debug("orchestration_engine_stop_failed", exc_info=True)

        # 1. Flush and close the LangGraph checkpointer (SQLite saver).
        #    Without this, pending checkpoints may not hit disk and the
        #    next daemon boot will not see the most recent turns.
        if self._checkpointer is not None:
            try:
                from kora_v2.runtime.checkpointer import close_checkpointer

                await close_checkpointer(self._checkpointer)
                log.info("checkpointer_closed_via_container")
            except Exception:
                log.debug("checkpointer_close_failed", exc_info=True)

        # 2. Close the projection DB.
        if self.projection_db is not None:
            try:
                await self.projection_db.close()
                log.info("projection_db_closed_via_container")
            except Exception:
                log.debug("projection_db_close_failed", exc_info=True)

        # 3. Unload the embedding model (releases GPU/MPS memory).
        if self.embedding_model is not None and self.embedding_model.is_loaded:
            try:
                self.embedding_model.unload()
                log.info("embedding_model_unloaded_via_container")
            except Exception:
                log.debug("embedding_model_unload_failed", exc_info=True)
