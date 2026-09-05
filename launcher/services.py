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
from events.log import attach_event_log


@dataclass
class Services:
    """Everything the runtime owns, after configuration."""

    config: dict
    bus: EventBus
    engine: Any                      # brain.chat_engine.ChatEngine
    memory: Any                      # memory.manager.MemoryManager
    event_log: Any = None            # events.log.EventLogger
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
    cognitive: Any = None            # core.cognitive.CognitiveStore
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

    # Immediately, and before anything that publishes. Until this line the
    # bus had no observer at all: `subscribe_all` existed with no
    # production caller while two docstrings claimed logging used it, so a
    # notification that never reached the phone left no trace to say
    # whether it was published, aged out of the outbox, or drained by
    # another device. Attached unconditionally rather than behind a flag,
    # because it writes at DEBUG - `logging.level` is already the owner's
    # control over whether any of it is visible, and a second switch would
    # let the two disagree.
    event_log = attach_event_log(bus)

    memory = memory if memory is not None else _build_memory()

    profile, knowledge, companion = _build_knowledge(config, memory)

    # The clock before anything that reads a time. One clock for the whole
    # process, so the memory pipeline, the proactive engine and the prompt
    # all agree on what "now" is - and so pinning it in a test pins all
    # three at once.
    clock = _build_clock(config)

    pipeline = _build_pipeline(config, memory, clock)

    # After the clock, deliberately. The store hands that clock to every
    # state it makes, so "when did that action happen" and "what time is
    # it in the prompt" are the same reading rather than two.
    cognitive = _build_cognitive(clock)

    vision = vision if vision is not None else _build_vision(config, bus)

    # Before the engine, and that ordering is load bearing: the engine is
    # handed the runner so a conversation can ask for a tool, and a runner
    # built afterwards would arrive too late to be injected.
    # After the pipeline as well as before the engine: `remember` writes
    # through it, and a runner built earlier would register the tool with
    # nothing behind it.
    # After the vision manager, and for the same reason as the pipeline:
    # `describe_screen` is built around the manager this process already
    # has, and a runner built earlier would register the tool with nothing
    # behind it - or worse, with a second manager of its own.
    tools = _build_tools(config, bus, pipeline, vision)

    engine = _build_engine(
        config, bus, vision, knowledge, memory, tools, clock, pipeline,
        cognitive,
    )

    # After the pipeline, which is where its pending work comes from.
    proactive = _build_proactive(config, bus, pipeline, clock, memory)

    tts = _build_tts(config, bus)

    stt = _build_stt(config, bus)

    avatar = _build_avatar(config, bus)

    # Last, deliberately. A plugin may register tools and subscribe to
    # events, so everything it might touch has to exist first.
    plugins = _build_plugins(config, bus, tools)

    return Services(
        config=config,
        bus=bus,
        event_log=event_log,
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
        cognitive=cognitive,
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
    cognitive=None,
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
      * `cognitive` - the one store of what the device agent has already
        done. Built here rather than inside the engine so that a second
        one cannot exist: two records of completed actions is how an agent
        opens the same app twice.
      * `verifier` - the Phase 4 claim -> evidence boundary. Built here
        for the same reason as the rest: it reads `response.verify` from
        *this* config dict, so a caller-supplied override is honoured
        instead of silently ignored.
    """

    from brain.chat_engine import ChatEngine
    from brain.router import BrainRouter

    llm_config = config.get("llm") or {}

    provider_name = llm_config.get("provider")

    llm = BrainRouter(provider_name=provider_name) if provider_name else None

    # Lanes are opt-in and, when the owner has configured none, add
    # nothing: a bare BrainRouter is handed over exactly as before. The
    # wrapper is built only when there is a lane to serve, so the default
    # install has one fewer object in the path, not one more - and no test
    # or caller that reaches for `.provider_name` or `._provider` can tell
    # the difference either way.
    lanes = {
        task: name
        for task, name in (llm_config.get("task_models") or {}).items()
        if isinstance(name, str) and name.strip()
    }

    if lanes and llm is not None:
        from brain.model_router import CapabilityRouter

        llm = CapabilityRouter(chat=llm, lanes=lanes)

    return ChatEngine(
        memory=memory,
        llm=llm,
        events=bus,
        vision=vision,
        knowledge=knowledge,
        tools=tools,
        clock=clock,
        pipeline=pipeline,
        cognitive=cognitive,
        verifier=_build_verifier(config),
    )


def _build_verifier(config: dict):
    """
    The response verifier, or None when the owner turned it off.

    None is a real configuration, not a degraded one: `ConversationManager`
    builds no evidence ledger without a verifier, so `enabled: false`
    produces exactly the pre-Phase-4 pipeline rather than a checked
    pipeline whose checks are ignored.

    A failure to import or construct it is logged and swallowed. A
    grounding layer that cannot start must cost its own function, never
    the whole chat engine - the same rule the verifier itself follows
    when a single verification fails.
    """

    settings = ((config.get("response") or {}).get("verify") or {})

    if not settings.get("enabled", True):
        logger.info("Response verifier disabled by configuration.")
        return None

    try:
        from brain.verify import ResponseVerifier

        repair = bool(settings.get("repair", True))

        if not repair:
            logger.info("Response verifier in observe-only mode (no repair).")

        return ResponseVerifier(repair=repair)

    except Exception as error:  # noqa: BLE001
        logger.warning("Response verifier unavailable: %s", error)
        return None


def _build_cognitive(clock):
    """
    The one store of what Aura is in the middle of.

    Always built, and unconditional on purpose: there is no configuration
    under which the agent should be allowed to forget that it already
    opened the app. It costs an empty dict until a device ticks.
    """

    from core.cognitive import CognitiveStore

    return CognitiveStore(clock=clock)


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


def _build_proactive(config: dict, bus, pipeline, clock, memory):
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

    The same store, read for a different category, is where appreciations
    come from. Both sources are absent without a pipeline and both then
    produce silence; the difference from before is that the appreciation
    source now exists at all. It did not, so that category could never
    fire in a real process however well its own tests passed.
    """

    from proactive import build_proactive_engine
    from proactive.ledger import SendLedger
    from proactive.memories import EpisodicMemorySource
    from proactive.tasks import EpisodicTaskSource

    tasks = None
    memories = None

    if pipeline is not None:
        tasks = EpisodicTaskSource(pipeline.episodic, clock=clock.now)

        # And the source without which one of the four categories could
        # not fire at all. `memories` was never passed here, so
        # `relevant_memories` was empty in every real process and the
        # appreciation branch was dead code that three tests covered
        # (sections 21, 44).
        memories = EpisodicMemorySource(pipeline.episodic, clock=clock.now)

    # Every limit the owner sets on proactive messaging - how many a day,
    # how long between them, no repeats - was enforced from a deque that
    # died with the process, so a restart handed back a clean slate and
    # the owner's cap was quietly unenforceable (sections 2, 19 and 20).
    # This is the only place that decides the history lives on disk; the
    # package itself stays file-free unless told otherwise.
    return build_proactive_engine(
        config,
        events=bus,
        pending_tasks=tasks,
        memories=memories,
        clock=clock,
        ledger=SendLedger(),
        # Whether the owner is actually absent, read from the messages
        # table rather than from a field that empties on restart. Without
        # it a restart reads as "away forever" and Aura greets somebody
        # who was mid-conversation a minute ago (sections 8, 19, 21).
        last_user_message=memory.last_said_at,
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

        from vision.capture import default_screen_capture

        monitor = settings.get("monitor", 1)

        # mss when installed, GDI through ctypes when not. Previously this
        # tried mss alone and logged "using window titles" - which on this
        # machine, where mss is not installed, meant the pixel half of
        # vision was unreachable code. The fallback needs nothing
        # installed, so `capture_screen: true` now does what it says on a
        # stock Windows machine.
        candidate = default_screen_capture(monitor=monitor)

        if candidate is not None:
            capture = candidate
            logger.info(
                "Screen capture: monitor %s via %s",
                monitor,
                type(candidate).__name__,
            )
        else:
            logger.info(
                "Screen capture unavailable on this platform, "
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
    The processor chain the manager will use, or None for titles only.

    Pixels are only worth a vision model if the encoder is installed.
    Returning None here is what makes a broken Pillow degrade to the
    window title description instead of an empty observation every turn.
    (Pillow is a hard requirement rather than an extra, so this branch
    guards a broken install or a Windows DLL load failure, not a normal
    one.)

    What comes back is a `ProcessorChain`, not a bare pixel processor,
    and that is phase 19's central change. A pixel processor alone
    *replaced* the window title description: an owner who set
    `capture_screen: true` while their Ollama daemon was down traded a
    working sentence for None, because `VisionManager.refresh` reads an
    empty description as "no observation". The chain puts the title
    processor underneath as the floor, so the worst case is the
    description the owner had before they turned the feature on.

    Order is local first, cloud second, titles last, and the order is a
    section 30 statement as much as a cost one: the model that runs on
    this machine gets first refusal on the owner's screen, and pixels
    only leave the machine when the local model had nothing to say.
    """

    try:
        import PIL                      # noqa: F401, PLC0415
    except ImportError:
        logger.info(
            "Vision: Pillow not installed, using window titles "
            "(pip install pillow for screen understanding)"
        )
        return None

    from vision.processor import ProcessorChain, WindowTitleProcessor

    pixels = [
        _build_ollama_vision(settings, config),
        _build_cloud_vision(settings, config),
    ]

    pixels = [p for p in pixels if p is not None]

    if not pixels:
        return None

    # The floor. Built here rather than left to the manager's default
    # because the manager only reaches its default when `processor` is
    # None, and from here on it never will be.
    return ProcessorChain([*pixels, WindowTitleProcessor()])


def _build_ollama_vision(settings: dict, config: dict):
    """
    The local pixel processor.

    The model comes from `vision.settings.ollama_model`, which is also
    what keeps this path from being handed the server's cloud model
    name: the two processors have separate keys (`ollama_model` and
    `cloud_model`) rather than one shared `vision.model`. The log line
    below is deliberately the model and the host together, so a
    mismatch is visible at startup rather than as an empty vision
    section later.

    Not gated on the daemon answering. A startup probe would add a
    connect to every launch and a second failure mode to read, and it
    would answer a question that has already changed by the time vision
    first runs - Ollama can be started after Aura. An unreachable daemon
    costs one refused connection per observation, and the chain behind
    it is what makes that cost a fall-through rather than a silence.
    """

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


def _build_cloud_vision(settings: dict, config: dict):
    """
    Cloud vision for the *desktop*, and only if the owner said so.

    Two gates, and the first one is section 30 rather than convenience.
    `build_cloud_vision_processor` builds a provider whenever
    `GEMINI_API_KEY` or `OPENROUTER_API_KEY` is in the environment - and
    a key sits in this repository's own `.env`, which is how a
    screenshot of the owner's desktop would start being uploaded to
    Google because somebody configured *text* generation. A key is
    permission to talk to a provider. It is not permission to send them
    a picture of this screen, so `send_screen_to_cloud` defaults to
    false and the owner turns it on by name.

    The phone path is not gated by this and is deliberately untouched:
    `server/runtime.py::_build_remote_vision` builds the same processor
    for frames a device uploaded, where the owner asked for exactly that
    by pointing the Android screen feature at their server. This key is
    about the machine Aura is running on.
    """

    if not settings.get("send_screen_to_cloud", False):
        return None

    from vision.cloud_processor import build_cloud_vision_processor

    processor = build_cloud_vision_processor(config)

    if processor is None:
        logger.warning(
            "Vision: vision.send_screen_to_cloud is on but no cloud vision "
            "provider is configured - set GEMINI_API_KEY or "
            "OPENROUTER_API_KEY, or turn the setting off"
        )
        return None

    # The one line an owner can audit for "is my screen leaving this
    # machine". Named providers, at info level, on every startup.
    logger.info(
        "Vision: desktop screenshots will be sent to %s "
        "(vision.send_screen_to_cloud is on)",
        ", ".join(
            getattr(p, "provider_name", type(p).__name__)
            for p in processor.providers
        ),
    )

    return processor


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

def _build_tools(config: dict, bus, pipeline=None, vision=None):
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

    return build_tools(
        config.get("tools") or {},
        events=bus,
        memory=pipeline,
        vision=vision,
    )


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
