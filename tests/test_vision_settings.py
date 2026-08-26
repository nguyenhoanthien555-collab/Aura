"""
Which vision model each processor is handed.

The bug this file exists to prevent is a quiet one. Before the split
there was a single `vision.model`, read by both the cloud processor and
the local Ollama one, and it held a hosted model name because that is
the deployment that runs. Nothing was broken, because `capture_screen`
is false and the local processor is therefore never built - the moment
someone turned capture on, a local daemon was asked for `gemini-3.6-flash`
and vision silently degraded to window titles.

So: two keys, one resolver module, and the tests below. They pin the
precedence rules rather than the shipped values, because the values are
config and the precedence is the contract - in particular that a config
file written before the split still resolves the way it used to. There
is no migration path for config files (docs/DEPLOYMENT.md), so the
legacy key has to keep working.
"""

import pytest

from vision.settings import DEFAULT_OLLAMA_MODEL, cloud_model, ollama_model


# ----------------------------------------------------------------------
# The two keys are independent
# ----------------------------------------------------------------------

def test_each_processor_gets_its_own_name():
    """The whole point: both can be right at the same time."""

    config = {
        "vision": {
            "cloud_model": "gemini-3.6-flash",
            "ollama_model": "qwen2.5vl:7b",
        }
    }

    assert cloud_model(config) == "gemini-3.6-flash"
    assert ollama_model(config) == "qwen2.5vl:7b"


def test_the_cloud_name_never_reaches_ollama():
    """
    The regression, stated directly. A config naming only a hosted
    model must not hand that name to a local daemon.
    """

    config = {"vision": {"cloud_model": "gemini-3.6-flash"}}

    assert ollama_model(config) == DEFAULT_OLLAMA_MODEL


def test_the_ollama_tag_never_reaches_the_cloud():
    """And the mirror image, which was equally possible."""

    config = {"vision": {"ollama_model": "qwen2.5vl:7b"}}

    assert cloud_model(config) == ""


# ----------------------------------------------------------------------
# Precedence
# ----------------------------------------------------------------------

def test_the_legacy_key_still_works_for_both():
    """A config file written before the split, unchanged."""

    config = {"vision": {"model": "gemini-3.6-flash"}}

    assert cloud_model(config) == "gemini-3.6-flash"
    assert ollama_model(config) == "gemini-3.6-flash"


@pytest.mark.parametrize(
    "resolve, key",
    [(cloud_model, "cloud_model"), (ollama_model, "ollama_model")],
    ids=["cloud", "ollama"],
)
def test_the_specific_key_beats_the_legacy_one(resolve, key):
    """Adding the new key is how a stale config gets fixed."""

    config = {"vision": {"model": "legacy", key: "specific"}}

    assert resolve(config) == "specific"


def test_the_cloud_falls_back_to_the_text_model():
    """
    A deployment that names one hosted model for text usually means the
    same family for images, and naming it twice is noise.
    """

    config = {"llm": {"model": "gemini-3.6-flash"}}

    assert cloud_model(config) == "gemini-3.6-flash"


def test_a_vision_key_beats_the_text_model():

    config = {
        "vision": {"cloud_model": "gemini-3.6-pro"},
        "llm": {"model": "gemini-3.6-flash"},
    }

    assert cloud_model(config) == "gemini-3.6-pro"


def test_ollama_ignores_the_text_model():
    """
    `llm.model` is a hosted name in every shipped config. Falling back
    to it here would reintroduce exactly the bug the split removed.
    """

    config = {"llm": {"model": "gemini-3.6-flash"}}

    assert ollama_model(config) == DEFAULT_OLLAMA_MODEL


# ----------------------------------------------------------------------
# Absent, empty and malformed
# ----------------------------------------------------------------------

@pytest.mark.parametrize("config", [{}, {"vision": {}}, {"vision": None}])
def test_nothing_configured_is_not_a_crash(config):
    """
    `vision: None` is what a YAML section with only comments under it
    parses to, and it reaches here as a real config value.
    """

    assert cloud_model(config) == ""
    assert ollama_model(config) == DEFAULT_OLLAMA_MODEL


def test_the_cloud_returns_empty_rather_than_guessing():
    """
    The caller asks the provider whether it supports vision, and ""
    fails that check. A guessed default would instead send an image to
    a model that cannot read one, and bill for it.
    """

    assert cloud_model({}) == ""


@pytest.mark.parametrize("blank", ["", None])
def test_a_blank_value_falls_through(blank):
    """`cloud_model:` with nothing after it means unset, not empty."""

    config = {"vision": {"cloud_model": blank, "model": "legacy"}}

    assert cloud_model(config) == "legacy"


# ----------------------------------------------------------------------
# The call sites
# ----------------------------------------------------------------------

def test_the_defaults_carry_a_usable_pair():
    """
    The shipped defaults have to be right for both processors without
    anyone editing anything - that they were not is what started this.
    """

    from core.config import DEFAULT_CONFIG

    assert ollama_model(DEFAULT_CONFIG) == "qwen2.5vl:7b"
    assert ":" in ollama_model(DEFAULT_CONFIG)          # an Ollama tag


def test_config_yaml_names_both():
    """
    The committed config, checked as a file. A default that is right
    and a config.yaml that overrides it with the old single key would
    pass every test above and still ship the bug.
    """

    from pathlib import Path

    import yaml

    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    vision = config.get("vision") or {}

    assert "model" not in vision, "the ambiguous key is back"

    assert cloud_model(config)
    assert ollama_model(config) == "qwen2.5vl:7b"


def test_the_desktop_processor_is_built_with_the_ollama_tag():
    """
    Through the composition root, not the resolver directly. The
    resolver being right does not help if the call site still reads
    `settings["model"]`.
    """

    pytest.importorskip("PIL", reason="Pillow is an optional vision extra")

    from launcher.services import _build_vision_processor

    config = {
        "vision": {
            "cloud_model": "gemini-3.6-flash",
            "ollama_model": "qwen2.5vl:7b",
        },
        "llm": {"model": "gemini-3.6-flash", "host": ""},
    }

    chain = _build_vision_processor(config["vision"], config)

    # A chain since 19.1, not a bare processor: pixel processors first,
    # WindowTitleProcessor last. Reach through it for the Ollama link
    # rather than relaxing the assertion, because the regression this
    # test exists for - the cloud name being handed to a local daemon -
    # is still exactly as possible one layer down.
    ollama = [
        processor
        for processor in chain.processors
        if type(processor).__name__ == "OllamaVisionProcessor"
    ]

    assert len(ollama) == 1, [type(p).__name__ for p in chain.processors]

    assert ollama[0].model == "qwen2.5vl:7b"


def test_the_cloud_processor_is_built_with_the_cloud_name(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    from vision.cloud_processor import build_cloud_vision_processor

    config = {
        "vision": {
            "cloud_model": "gemini-3.6-flash",
            "ollama_model": "qwen2.5vl:7b",
        }
    }

    processor = build_cloud_vision_processor(config)

    assert processor is not None
    assert "qwen" not in processor.providers[0].model
