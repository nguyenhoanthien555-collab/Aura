"""
Service construction.

The runtime composition root: reads config, builds every optional
subsystem, and hands back one bundle. This is the only module that
imports brain, memory, voice, vision, avatar and tools together - which
is precisely why none of those packages import each other.

Every subsystem is allowed to be absent. A machine with no display, no
microphone and no speakers gets a Services with `avatar` rendering to
nothing and `tts`/`stt` set to None, and Aura still runs as a text
companion.
"""

from dataclasses import dataclass
from typing import Any

from core.config import load_config
from core.logger import apply_config_level, logger

from events.bus import EventBus


@dataclass
class Services:
    """Everything the runtime owns, after configuration."""

    config: dict
    bus: EventBus
    engine: Any                      # brain.chat_engine.ChatEngine
    memory: Any                      # memory.manager.MemoryManager
    profile: Any = None              # memory.profile.ProfileStore
    knowledge: Any = None            # MemoryKnowledgeProvider
    companion: Any = None            # memory.companion.CompanionMemory
    clock: Any = None                # core.temporal.TemporalClock
    pipeline: Any = None             # memory.pipeline.MemoryPipeline
    proactive: Any = None            # proactive.engine.ProactiveEngine
    vision: Any = None               # vision.manager.VisionManager
    tts: Any = None                  # voice.tts.engine.TTSEngine
    stt: Any = None                  # voice.stt.engine.SpeechToTextEngine
    tools: Any = None                # tools.executor.ToolExecutor
    avatar: Any = None               # avatar.controller.AvatarController
    plugins: Any = None              # plugins.manager.PluginManager

    def summary(self) -> str:
        """One line description of what actually came up."""

        parts = [
            f"llm={getattr(self.engine.conversation.llm, 'provider_name', '?')}",
            f"voice={'on' if self.tts else 'off'}",
            f"mic={'on' if self.stt else 'off'}",
            f"vision={'on' if self.vision and self.vision.enabled else 'off'}",
            f"tools={len(self.tools.available()) if self.tools else 0}",
            f"avatar={type(self.avatar.renderer).__name__ if self.avatar else 'none'}",
            # `is not None`, not truthiness: PluginManager defines __len__,
            # so a manager that discovered nothing is falsy.
            f"plugins={len(self.plugins.enabled) if self.plugins is not None else 0}",
        ]

        return "  ".join(parts)


def build_services(
    config: dict | None = None,
    bus: EventBus | None = None,
    memory: Any = None,
    vision: Any = None,
) -> Services:
    """
    Build everything from config.

    Order matters only in one place: the bus exists before anything that
    publishes to it.

    `memory` and `vision` may be supplied to point the system at a
    different implementation of the same port:

      * memory - an isolated in-memory database in tests, or a mounted
        volume in a container. None opens the default `data/memory.db`.

      * vision - the remote screen source in server mode, where the
        observations come from a phone rather than from this machine's
        display. None builds from the `vision:` config section.
    """

    config = config if config is not None else load_config()

    # Before anything else builds: `logging.level` in config.yaml has to
    # be in force for the construction messages below to obey it.
    apply_config_level(config)

    bus = bus or EventBus()

    memory = memory if memory is not None else _build_memory()

    profile, knowledge, companion = _build_knowledge(config, memory)

    # The clock before anything that reads a time. One clock for the whole
    # process, so the memory pipeline, the proactive engine and the prompt
    # all agree on what "now" is - and so pinning it in a test pins all
    # three at once.
    clock = _build_clock(config)

    pipeline = _build_pipeline(config, memory, clock)

    vision = vision if vision is not None else _build_vision(config, bus)

    # Before the engine, and that ordering is load bearing: the engine is
    # handed the runner so a conversation can ask for a tool, and a runner
    # built afterwards would arrive too late to be injected.
    tools = _build_tools(config, bus)

    engine = _build_engine(
        config, bus, vision, knowledge, memory, tools, clock, pipeline
    )

    # After the pipeline, which is where its pending work comes from.
    proactive = _build_proactive(config, bus, pipeline, clock)

    tts = _build_tts(config, bus)

    stt = _build_stt(config, bus)

    avatar = _build_avatar(config, bus)

    # Last, deliberately. A plugin may register tools and subscribe to
    # events, so everything it might touch has to exist first.
    plugins = _build_plugins(config, bus, tools)

    return Services(
        config=config,
        bus=bus,
        engine=engine,
        memory=memory,
        profile=profile,
        knowledge=knowledge,
        companion=companion,
        clock=clock,
        pipeline=pipeline,
        proactive=proactive,
        vision=vision,
        tts=tts,
        stt=stt,
        tools=tools,
        avatar=avatar,
        plugins=plugins,
    )


# ----------------------------------------------------------------------
# Brain and memory
# ----------------------------------------------------------------------

def _build_memory():

    from memory.manager import MemoryManager

    return MemoryManager()


def _build_engine(
    config: dict,
    bus,
    vision,
    knowledge,
    memory,
    tools=None,
    clock=None,
    pipeline=None,
):
    """
    The chat engine, wired to the objects this composition root already
    built.

    Five things are injected rather than left to ChatEngine's defaults:

      * `memory` - so the runtime and the conversation share one
        MemoryManager instead of opening two.
      * `llm` - so the provider comes from *this* config dict. Left to
        its default, BrainRouter re-reads config.yaml from disk, which
        silently ignored `--provider` and any caller-supplied override.
      * `tools` - the same executor `Services.tools` exposes, so a tool
        run by the conversation and one run from the CLI pass the identical
        policy, allow list and confirmation handler. Two executors would
        mean two sets of permissions, one of which nobody configured.
      * `clock` - the one clock the whole process shares, so the time in
        the prompt and the time on a stored memory cannot disagree.
      * `pipeline` - the same memory pipeline the proactive engine reads
        its pending work from. Two pipelines would mean a reminder about
        work recorded in a database nobody is reading.
    """

    from brain.chat_engine import ChatEngine
    from brain.router import BrainRouter

    llm_config = config.get("llm") or {}

    provider_name = llm_config.get("provider")

    llm = BrainRouter(provider_name=provider_name) if provider_name else None

    return ChatEngine(
        memory=memory,
        llm=llm,
        events=bus,
        vision=vision,
        knowledge=knowledge,
        tools=tools,
        clock=clock,
        pipeline=pipeline,
    )


def _build_clock(config: dict):
    """
    The application clock.

    Always built - there is no configuration under which Aura should not
    know what time it is - but the timezone it reports comes from
    `temporal.timezone`, defaulting to this machine's.
    """

    from core.temporal import TemporalClock

    return TemporalClock.from_config(config)


def _build_pipeline(config: dict, memory, clock):
    """
    Memory 2.0: episodic store, temporary context and user model.

    Shares the conversation session, so the transcript, the profile, the
    episodes and the user model are one database file and one transaction
    scope. Off when `memory.pipeline` is false, in which case Aura keeps
    the transcript and the older profile recall and nothing else.

    The bundled profile is seeded here rather than on first use, because
    seeding on first use makes the first turn after a fresh install
    behave differently from every turn after it. Idempotent, so a restart
    costs one indexed query per entry and changes nothing.
    """

    settings = config.get("memory") or {}

    if not settings.get("pipeline", True):
        return None

    from memory.pipeline import build_memory_pipeline

    session = getattr(memory, "session", None)

    # The Phase 8 tables, created here rather than at database init, so a
    # deployment that leaves the pipeline off never grows them. Additive
    # and idempotent; see memory/sqlite.py.
    if session is not None:
        from memory.sqlite import init_pipeline_tables

        try:
            init_pipeline_tables(session.get_bind())
        except Exception as error:
            logger.warning("Could not create the memory pipeline tables: %s", error)
            return None

    pipeline = build_memory_pipeline(config, session=session, clock=clock)

    if settings.get("seed_profile", True):
        try:
            pipeline.ensure_profile()
        except Exception as error:
            logger.warning("User model seeding failed: %s", error)

    return pipeline


def _build_proactive(config: dict, bus, pipeline, clock):
    """
    The proactive engine.

    Always built, even when proactive messaging is switched off: a
    disabled engine still ticks and still decides not to speak, which is
    simpler than every caller checking for None. The `enabled` flag lives
    in the policy with the other gates, and defaults to off.

    Its pending work comes from the memory pipeline's episodic store and
    nowhere else. With no pipeline there is no task source, and with no
    task source there are no task reminders - which is the correct
    behaviour rather than a gap, since the alternative is guessing at
    what the user might have been doing.
    """

    from proactive import build_proactive_engine
    from proactive.tasks import EpisodicTaskSource

    tasks = None

    if pipeline is not None:
        tasks = EpisodicTaskSource(pipeline.episodic, clock=clock.now)

    return build_proactive_engine(
        config,
        events=bus,
        pending_tasks=tasks,
        clock=clock,
    )


def _build_knowledge(config: dict, memory):
    """
    Long term memory, combining two sources:

    1. ProfileStore + KeywordRetriever (SQLite-backed facts + recall)
    2. CompanionMemory (in-memory companion context)

    Both implement KnowledgeProvider and are composed into one that merges
    their lines. Shares the conversation session so profile facts and the
    transcript live in one database file and one transaction scope.

    Returns (profile, knowledge, companion). The companion store is handed
    back separately so a caller can populate it - nothing else can reach
    inside the composite to do so.
    """

    settings = config.get("memory") or {}

    use_profile = bool(settings.get("profile", True))
    use_recall = bool(settings.get("recall", False))
    use_companion = bool(settings.get("companion", True))

    if not use_profile and not use_recall and not use_companion:
        return None, None, None

    from memory.knowledge import MemoryKnowledgeProvider
    from memory.profile import ProfileStore
    from memory.retrieval import KeywordRetriever, NullRetriever
    from memory.companion import CompanionMemory

    session = getattr(memory, "session", None)

    profile = ProfileStore(session=session) if use_profile else None

    if use_recall:
        retriever = KeywordRetriever(
            session=session,
            skip_recent=settings.get("history_limit", 20),
        )
    else:
        retriever = NullRetriever()

    # Profile facts + recalled messages
    durable = MemoryKnowledgeProvider(
        profile=profile,
        retriever=retriever,
        max_facts=settings.get("max_facts", 8),
        max_recalled=settings.get("max_recalled", 3),
    )

    # Companion context (facts, preferences, goals, projects, style, highlights)
    companion = CompanionMemory(
        max_lines=settings.get("max_companion", 10),
        max_highlights=settings.get("max_highlights", 3),
    ) if use_companion else None

    # Compose both into one knowledge provider
    if companion:
        knowledge = _CompositeKnowledge(durable, companion)
    else:
        knowledge = durable

    return profile, knowledge, companion


class _CompositeKnowledge:
    """
    Merges two knowledge providers into one.

    ProfileStore lives in SQLite and persists across sessions.
    CompanionMemory is in-memory and session-only for now.

    Both are queried, both contribute lines, and max_lines is applied
    after merging so neither source can crowd out the other.
    """

    def __init__(self, durable, companion, max_total: int = 20):
        self.durable = durable
        self.companion = companion
        self.max_total = max_total

    def get_knowledge(self, query: str) -> list[str]:

        lines: list[str] = []

        # Durable first: who the user is outranks what the session contains
        try:
            lines.extend(self.durable.get_knowledge(query) or [])
        except Exception as error:
            logger.debug("Durable knowledge lookup failed: %s", error)

        # Companion second: projects, goals, preferences, coding style
        try:
            lines.extend(self.companion.get_knowledge(query) or [])
        except Exception as error:
            logger.debug("Companion knowledge lookup failed: %s", error)

        return lines[: self.max_total]


# ----------------------------------------------------------------------
# Vision
# ----------------------------------------------------------------------

def _build_vision(config: dict, bus):
    settings = config.get("vision") or {}

    if not settings.get("enabled", False):
        return None

    from vision.manager import VisionManager

    capture = None

    if settings.get("capture_screen", False):

        from vision.capture import ScreenshotCapture

        monitor = settings.get("monitor", 1)

        candidate = ScreenshotCapture(monitor=monitor)

        if candidate.is_available():
            capture = candidate
            logger.info("Screen capture: monitor %s via mss", monitor)
        else:
            logger.info(
                "Screen capture unavailable (mss not installed), "
                "using window titles"
            )

    processor = (
        _build_vision_processor(settings, config) if capture else None
    )

    return VisionManager(
        capture=capture,
        processor=processor,
        events=bus,
        enabled=True,
        min_interval=settings.get(
            "min_interval",
            2.0
        ),
    )


def _build_vision_processor(settings: dict, config: dict):
    """
    The pixel processor, or None to leave the manager on window titles.

    Pixels are only worth a vision model if the encoder is installed.
    Returning None here is what makes a missing Pillow degrade to the
    window title description instead of an empty observation every turn.

    The model comes from `vision.settings.ollama_model`, which is also
    what keeps this path from being handed the server's cloud model
    name: the two processors have separate keys (`ollama_model` and
    `cloud_model`) rather than one shared `vision.model`. The log line
    below is deliberately the model and the host together, so a
    mismatch is visible at startup rather than as an empty vision
    section later.
    """

    try:
        import PIL                      # noqa: F401, PLC0415
    except ImportError:
        logger.info(
            "Vision: Pillow not installed, using window titles "
            "(pip install pillow for screen understanding)"
        )
        return None

    from vision.ollama_processor import (
        DEFAULT_HOST,
        DEFAULT_TIMEOUT,
        OllamaVisionProcessor,
    )
    from vision.settings import ollama_model

    llm = config.get("llm") or {}

    model = ollama_model(config)
    host = settings.get("host") or llm.get("host") or DEFAULT_HOST
    timeout = settings.get("timeout") or DEFAULT_TIMEOUT

    debug_path = settings.get("debug_frame") or None

    logger.info("Vision: %s at %s", model, host)

    return OllamaVisionProcessor(
        model=model,
        host=host,
        timeout=timeout,
        debug_path=debug_path,
    )


# ----------------------------------------------------------------------
# Voice
# ----------------------------------------------------------------------

def _build_tts(config: dict, bus):
    """
    Build the speaking half and subscribe it to replies.

    `attach` is what makes voice consume Response without the brain
    knowing TTS exists.
    """

    settings = ((config.get("voice") or {}).get("tts")) or {}

    if not settings.get("enabled", False):
        return None

    from voice.factory import create_tts_provider
    from voice.tts.engine import TTSEngine

    provider = create_tts_provider(
        settings.get("provider", "auto"),
        settings,
    )

    engine = TTSEngine(provider=provider, events=bus)

    engine.attach(bus)

    logger.info("Voice output: %s", type(provider).__name__)

    return engine


def _build_stt(config: dict, bus):

    voice = config.get("voice") or {}

    settings = voice.get("stt") or {}

    if not settings.get("enabled", False):
        return None

    from voice.factory import create_microphone, create_stt_provider
    from voice.stt.engine import KeywordWakeWord, SpeechToTextEngine

    provider = create_stt_provider(
        settings.get("provider", "mock"),
        settings,
    )

    microphone = create_microphone(voice.get("microphone") or {})

    phrase = (settings.get("wake_word") or "").strip()

    engine = SpeechToTextEngine(
        provider=provider,
        microphone=microphone,
        events=bus,
        wake_word=KeywordWakeWord([phrase]) if phrase else None,
        record_seconds=settings.get("record_seconds", 5.0),
    )

    if not engine.is_available():
        logger.info("Voice input configured but unavailable")

    return engine


# ----------------------------------------------------------------------
# Tools and avatar
# ----------------------------------------------------------------------

def _build_tools(config: dict, bus):
    """
    The one executor everything in this process runs tools through.

    No confirmation handler is attached here, and which composition root
    is running decides what that means:

      * Desktop. `launcher/cli.py` calls `set_tool_confirmation` as it
        starts, so a SENSITIVE or DANGEROUS call reaches a prompt at a
        terminal and waits for an explicit yes.

      * Server. Nothing installs one, and nothing should. There is no
        human at the other end of an HTTP request to ask, and a handler
        that answered on their behalf would be approval by default -
        every gated tool granted to whoever holds the auth token. With
        no handler, `ToolExecutor._approved` refuses anything outside
        `auto_approve`, which is SAFE only unless config says otherwise.

    So a server deployment can run safe tools and cannot run risky ones.
    That is the intended behaviour, not an oversight; the way to grant
    more is to widen `tools.auto_approve` in config on purpose, not to
    make something up here (AURA-P1-002).
    """

    from tools.factory import build_tools

    return build_tools(config.get("tools") or {}, events=bus)


def _build_plugins(config: dict, bus, tools):
    """
    Load and initialize plugins.

    A plugin receives the bus and the tool *registry*, never the executor.
    The distinction is the whole point: a plugin may add a capability, and
    it may not decide whether that capability is permitted. Registration
    and permission stay where they were - a plugin's tool still has to be
    named in `tools.allowed` before it can run.

    Returns None when nothing was discovered or configured, which keeps
    `Services.plugins` falsy on a stock install.
    """

    settings = config.get("plugins") or {}

    if not settings.get("enabled"):
        return None

    from plugins.factory import build_plugins

    registry = getattr(tools, "registry", None)

    return build_plugins(config, bus=bus, tools=registry)


def _build_avatar(config: dict, bus):

    settings = config.get("avatar") or {}

    from avatar.controller import AvatarController, create_renderer

    renderer = create_renderer(
        enabled=bool(settings.get("enabled", True)),
        options=settings,
    )

    controller = AvatarController(renderer=renderer)

    controller.attach(bus)

    return controller
