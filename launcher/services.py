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
from core.logger import logger

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
) -> Services:
    """
    Build everything from config.

    Order matters only in one place: the bus exists before anything that
    publishes to it.
    """

    config = config if config is not None else load_config()

    bus = bus or EventBus()

    memory = _build_memory()

    profile, knowledge, companion = _build_knowledge(config, memory)

    vision = _build_vision(config, bus)

    engine = _build_engine(bus, vision, knowledge)

    tts = _build_tts(config, bus)

    stt = _build_stt(config, bus)

    tools = _build_tools(config, bus)

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


def _build_engine(bus, vision, knowledge):

    from brain.chat_engine import ChatEngine

    return ChatEngine(
        events=bus,
        vision=vision,
        knowledge=knowledge,
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
        except Exception:
            pass

        # Companion second: projects, goals, preferences, coding style
        try:
            lines.extend(self.companion.get_knowledge(query) or [])
        except Exception:
            pass

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

        candidate = ScreenshotCapture(monitor=settings.get("monitor", 1))

        if candidate.is_available():
            capture = candidate
        else:
            logger.info("Screen capture unavailable, using window titles")

    return VisionManager(
        capture=capture,
        events=bus,
        enabled=True,
        min_interval=settings.get("min_interval", 2.0),
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

    from tools.factory import build_tools

    # No confirmation handler is attached here. The CLI installs one
    # when it starts, so approval always belongs to something that can
    # actually reach a human.
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
