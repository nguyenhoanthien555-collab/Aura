"""
Error visibility.

A subsystem that fails quietly is indistinguishable from a subsystem
that had nothing to say. These tests pin the difference: every optional
collaborator still degrades the turn rather than failing it, and every
one of those degradations now leaves a record.
"""

import logging

import pytest

from brain.conversation import ConversationManager
from brain.prompt_builder import PromptBuilder
from brain.providers.errors import (
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from brain.providers.fallback import ACCOUNT_LIMIT, FallbackProvider, _category_of
from core.logger import (
    DEFAULT_LEVEL,
    LOG_LEVEL_ENV,
    apply_config_level,
    resolve_level,
    setup_logger,
)


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------

class StubMemory:

    def __init__(self):
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit=10):
        return []


class StubLLM:

    provider_name = "stub"

    def __init__(self, reply="ok"):
        self.reply = reply

    def generate(self, prompt):
        return self.reply


class BrokenEvents:

    def publish(self, event):
        raise RuntimeError("bus is down")


class BrokenVision:

    def get_context(self):
        raise RuntimeError("no display")


class BrokenKnowledge:

    def get_knowledge(self, query):
        raise RuntimeError("database is locked")


class BrokenStyle:

    def hint(self):
        return ""

    def style(self, text):
        raise RuntimeError("style rules failed to compile")


class FailingProvider:

    def __init__(self, name, error):
        self.provider_name = name
        self.error = error

    def generate(self, prompt):
        raise self.error


class WorkingProvider:

    provider_name = "working"

    def generate(self, prompt):
        return "reply from working provider"


def build_manager(**kwargs):
    return ConversationManager(
        memory=StubMemory(),
        builder=PromptBuilder(),
        llm=StubLLM(),
        **kwargs,
    )


# ----------------------------------------------------------------------
# AURA-P1-015 - optional collaborators log why they degraded
# ----------------------------------------------------------------------

def test_broken_event_bus_is_logged_and_turn_survives(caplog):

    manager = build_manager(events=BrokenEvents())

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        response = manager.chat("hello")

    assert response.text == "ok"
    assert "Event publish failed" in caplog.text


def test_broken_vision_is_logged_and_turn_survives(caplog):

    manager = build_manager(vision=BrokenVision())

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        response = manager.chat("hello")

    assert response.text == "ok"
    assert "Vision context unavailable" in caplog.text
    assert "no display" in caplog.text


def test_broken_knowledge_is_logged_and_turn_survives(caplog):

    manager = build_manager(knowledge=BrokenKnowledge())

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        response = manager.chat("hello")

    assert response.text == "ok"
    assert "Knowledge lookup failed" in caplog.text
    assert "database is locked" in caplog.text


def test_broken_style_is_logged_and_reply_is_unstyled(caplog):

    manager = build_manager(style=BrokenStyle())

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        response = manager.chat("hello")

    # The answer survives; only its polish is lost.
    assert response.text == "ok"
    assert "Style pass failed" in caplog.text


def test_healthy_turn_logs_no_collaborator_failures(caplog):
    """Degradation logging must not fire on a working system."""

    manager = build_manager()

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        manager.chat("hello")

    for phrase in (
        "Event publish failed",
        "Vision context unavailable",
        "Knowledge lookup failed",
        "Style pass failed",
    ):
        assert phrase not in caplog.text


def test_collaborator_failures_do_not_stop_the_turn_being_saved():
    """Graceful degradation is unchanged: the turn is still persisted."""

    memory = StubMemory()

    manager = ConversationManager(
        memory=memory,
        builder=PromptBuilder(),
        llm=StubLLM(),
        events=BrokenEvents(),
        vision=BrokenVision(),
        knowledge=BrokenKnowledge(),
        style=BrokenStyle(),
    )

    assert manager.chat("hello").text == "ok"
    assert memory.saved == [("user", "hello"), ("assistant", "ok")]


# ----------------------------------------------------------------------
# AURA-P1-011 - fallback diagnostics keep the real exception type
# ----------------------------------------------------------------------

def test_category_of_account_limit():

    error = ProviderRateLimitError("quota gone", is_account_limit=True)

    assert _category_of(error) == ACCOUNT_LIMIT


def test_category_of_rate_limit():

    assert _category_of(
        ProviderRateLimitError("slow down")
    ) == "model/provider rate limit"


def test_category_of_unavailable():

    assert _category_of(
        ProviderUnavailableError("502")
    ) == "transient/unavailable"


def test_unknown_error_is_not_reported_as_an_auth_problem():
    """
    The regression this test exists for: a TimeoutError used to be logged
    as an "authentication/configuration error", sending whoever read the
    log looking for a key problem that was not there.
    """

    assert _category_of(TimeoutError("read timed out")) == "unclassified provider error"


def test_fallback_logs_the_actual_exception_type(caplog):

    chain = FallbackProvider(
        [FailingProvider("first", TimeoutError("read timed out")), WorkingProvider()],
        "first->working",
    )

    with caplog.at_level(logging.WARNING, logger="Aura"):
        assert chain.generate("hi") == "reply from working provider"

    assert "TimeoutError" in caplog.text
    assert "unclassified provider error" in caplog.text
    assert "authentication" not in caplog.text


def test_account_limit_still_stops_failover(caplog):
    """The short-circuit must survive the diagnostics change."""

    working = WorkingProvider()

    chain = FallbackProvider(
        [
            FailingProvider(
                "first",
                ProviderRateLimitError("quota gone", is_account_limit=True),
            ),
            working,
        ],
        "first->working",
    )

    with caplog.at_level(logging.WARNING, logger="Aura"):
        with pytest.raises(ProviderRateLimitError):
            chain.generate("hi")

    assert "stopping provider failover immediately" in caplog.text


# ----------------------------------------------------------------------
# AURA-P2-001 - the log level is configurable
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("DEBUG", logging.DEBUG),
        ("debug", logging.DEBUG),
        ("  Warning  ", logging.WARNING),
        ("WARN", logging.WARNING),
        ("10", 10),
        (10, 10),
        (None, None),
        ("", None),
        ("LOUDER", None),
        (True, None),
        ("handlers", None),
    ],
)
def test_resolve_level(value, expected):
    assert resolve_level(value) == expected


@pytest.fixture
def restore_level():
    """Put the shared logger back however this test left it."""

    before = logging.getLogger("Aura").level

    yield

    logging.getLogger("Aura").setLevel(before)


def test_config_level_is_applied(monkeypatch, restore_level):

    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)

    assert apply_config_level({"logging": {"level": "DEBUG"}}) == logging.DEBUG
    assert logging.getLogger("Aura").level == logging.DEBUG


def test_environment_variable_outranks_config(monkeypatch, restore_level):

    monkeypatch.setenv(LOG_LEVEL_ENV, "ERROR")

    logging.getLogger("Aura").setLevel(logging.ERROR)

    assert apply_config_level({"logging": {"level": "DEBUG"}}) == logging.ERROR
    assert logging.getLogger("Aura").level == logging.ERROR


def test_unreadable_level_warns_and_keeps_the_current_one(
    monkeypatch, restore_level, caplog
):

    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)

    logging.getLogger("Aura").setLevel(logging.INFO)

    # Deliberately not caplog.at_level: that fixture would set the level
    # on the very logger this test is asserting about. INFO already lets
    # a warning through to the propagated capture handler.
    assert apply_config_level({"logging": {"level": "LOUDER"}}) == logging.INFO
    assert logging.getLogger("Aura").level == logging.INFO

    assert "Unreadable logging level" in caplog.text


def test_absent_logging_section_changes_nothing(monkeypatch, restore_level):

    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)

    logging.getLogger("Aura").setLevel(DEFAULT_LEVEL)

    assert apply_config_level({}) == DEFAULT_LEVEL
    assert apply_config_level(None) == DEFAULT_LEVEL


def test_composition_root_applies_the_configured_level(
    monkeypatch, restore_level
):
    """
    The wiring, not the function.

    `apply_config_level` being correct is worth nothing if nobody calls
    it, and the only caller is `build_services`. That is the same shape of
    bug as an auth guard placed after the early return: the unit passes
    and the deployment is still wrong. So this drives the real composition
    root and reads the level off the shared logger afterwards.
    """

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from launcher.services import build_services
    from memory.models import Base
    from memory.manager import MemoryManager

    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)

    logging.getLogger("Aura").setLevel(logging.INFO)

    # Injected so the composition root does not open data/memory.db.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    try:
        build_services(
            config={
                "logging": {"level": "DEBUG"},
                # Everything optional stays off: this test is about one
                # line of build_services, not about the subsystems.
                "voice": {"tts": {"enabled": False}, "stt": {"enabled": False}},
                "vision": {"enabled": False},
                "avatar": {"enabled": False},
                "tools": {"enabled": False},
                "plugins": {"enabled": []},
                "memory": {"recall": False, "profile": False},
            },
            memory=MemoryManager(session=session),
        )

        assert logging.getLogger("Aura").level == logging.DEBUG

    finally:
        session.close()


@pytest.fixture
def fresh_logger():
    """
    Let `setup_logger` run again.

    It returns early once handlers exist, and importing core.logger
    already attached one - so without stripping them the function under
    test does nothing at all. `hasHandlers` also walks up to the root,
    which pytest has attached to, so propagation has to be off for the
    check to see an empty chain. All three are put back.
    """

    aura = logging.getLogger("Aura")

    handlers = list(aura.handlers)
    level = aura.level
    propagate = aura.propagate

    aura.handlers.clear()
    aura.propagate = False

    yield aura

    aura.handlers.clear()
    aura.handlers.extend(handlers)
    aura.setLevel(level)
    aura.propagate = propagate


def test_environment_sets_the_startup_level(monkeypatch, fresh_logger):
    """
    The only verbosity control that exists before config.yaml is read.

    A deployed container that cannot edit the config file it shipped with
    still has this one, and it governs the construction messages that a
    config-file level arrives too late to affect.
    """

    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")

    assert setup_logger().level == logging.DEBUG


def test_unreadable_environment_level_falls_back_to_the_default(
    monkeypatch, fresh_logger
):
    """A typo in an env var must not decide how much Aura logs."""

    monkeypatch.setenv(LOG_LEVEL_ENV, "LOUDER")

    assert setup_logger().level == DEFAULT_LEVEL


def test_setup_logger_does_not_stack_handlers(monkeypatch, fresh_logger):
    """
    Imported from a dozen modules, called once.

    Without the guard every import would add a handler and each line
    would be printed as many times as there are handlers.
    """

    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)

    first = setup_logger()

    assert len(first.handlers) == 1

    assert setup_logger() is first
    assert len(first.handlers) == 1


# ----------------------------------------------------------------------
# AURA-P1-012 - a collapsed provider chain says so, without leaking keys
# ----------------------------------------------------------------------

def _clear_provider_keys(monkeypatch):
    for variable in (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_skipped_fallback_providers_are_reported(monkeypatch, caplog):

    from brain.router import BrainRouter

    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")

    monkeypatch.setattr(
        "brain.router.load_config",
        lambda: {
            "llm": {
                "provider": "gemini",
                "fallback_providers": ["groq", "mistral", "openrouter"],
            }
        },
    )

    router = BrainRouter(provider_name="gemini")

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        provider = router.provider

    # Nothing was available to fall back to, so the chain is bare Gemini.
    assert type(provider).__name__ == "GeminiProvider"

    assert "GROQ_API_KEY is not set" in caplog.text
    assert "MISTRAL_API_KEY is not set" in caplog.text
    assert "OPENROUTER_API_KEY is not set" in caplog.text

    # The key itself is never written anywhere.
    assert "test-key-value" not in caplog.text


def test_chain_membership_is_logged(monkeypatch, caplog):

    from brain.router import BrainRouter

    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")

    monkeypatch.setattr(
        "brain.router.load_config",
        lambda: {
            "llm": {
                "provider": "gemini",
                "fallback_providers": ["groq", "openrouter"],
            }
        },
    )

    router = BrainRouter(provider_name="gemini")

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        provider = router.provider

    assert type(provider).__name__ == "FallbackProvider"
    assert provider.provider_name == "gemini->groq"

    assert "requested: gemini, groq, openrouter" in caplog.text
    assert "initialized: gemini, groq" in caplog.text
    assert "OPENROUTER_API_KEY is not set" in caplog.text

    assert "gemini-key" not in caplog.text
    assert "groq-key" not in caplog.text


def test_primary_named_again_as_a_fallback_is_explained(monkeypatch, caplog):

    from brain.router import BrainRouter

    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")

    monkeypatch.setattr(
        "brain.router.load_config",
        lambda: {"llm": {"provider": "gemini", "fallback_providers": ["gemini"]}},
    )

    router = BrainRouter(provider_name="gemini")

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        router.provider

    assert "gemini (already the primary)" in caplog.text


def test_skip_reason_never_reveals_a_value(monkeypatch):

    from brain.router import BrainRouter

    monkeypatch.setenv("GROQ_API_KEY", "super-secret")

    # Key present, so the reason is about construction, not the key.
    reason = BrainRouter._skip_reason("groq")

    assert "super-secret" not in reason
    assert reason == "initialization failed"

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert BrainRouter._skip_reason("groq") == "GROQ_API_KEY is not set"

    assert BrainRouter._skip_reason("nonexistent") == "unknown provider"
