"""
Which vision model the cloud processor is handed.

There used to be two keys here - `vision.cloud_model` for the hosted
processor and `vision.ollama_model` for a local Ollama one - because a
single `vision.model` had to be one or the other and silently
misconfigured whichever processor it was not written for. The local
processor is gone (Aura is cloud-only), so only `cloud_model` remains.

The tests below pin the precedence rules rather than the shipped values,
because the values are config and the precedence is the contract - in
particular that a config file written before the split, naming the
legacy `vision.model`, still resolves the way it used to. There is no
migration path for config files (docs/DEPLOYMENT.md), so the legacy key
has to keep working.
"""

import pytest

from vision.settings import cloud_model


# ----------------------------------------------------------------------
# Precedence
# ----------------------------------------------------------------------

def test_the_legacy_key_still_works():
    """A config file written before the split, unchanged."""

    config = {"vision": {"model": "gemini-3.6-flash"}}

    assert cloud_model(config) == "gemini-3.6-flash"


def test_the_specific_key_beats_the_legacy_one():
    """Adding the new key is how a stale config gets fixed."""

    config = {"vision": {"model": "legacy", "cloud_model": "specific"}}

    assert cloud_model(config) == "specific"


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

def test_config_yaml_names_the_cloud_model():
    """
    The committed config, checked as a file. The ambiguous single key
    and the removed local key must both be gone, and a cloud model must
    be named.
    """

    from pathlib import Path

    import yaml

    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    vision = config.get("vision") or {}

    assert "model" not in vision, "the ambiguous key is back"
    assert "ollama_model" not in vision, "the local key is back"

    assert cloud_model(config)


def test_the_cloud_processor_is_built_with_the_cloud_name(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    from vision.cloud_processor import build_cloud_vision_processor

    config = {"vision": {"cloud_model": "gemini-3.6-flash"}}

    processor = build_cloud_vision_processor(config)

    assert processor is not None
    assert processor.providers[0].model == "gemini-3.6-flash"
