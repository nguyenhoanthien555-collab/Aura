"""
Configuration tests.

Two things matter here, and neither is about YAML parsing.

First: safe defaults. A fresh Aura must not watch the screen, must not
run tools, and must not require a microphone. Those are asserted
individually below so that loosening one is a deliberate edit to a test,
not a quiet change to a dictionary.

Second: forward compatibility. A config.yaml written for an earlier
sprint must keep working. deep_merge is what provides that, so it is
tested on its own rather than only through load_config.
"""

import pytest
import yaml

import core.config as config_module
from core.config import DEFAULT_CONFIG, deep_merge, load_config, save_default_config

from tools.executor import ToolPolicy
from tools.factory import build_tools


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """
    Redirect config loading at a throwaway file.

    load_config() creates config.yaml when it is missing, so without this
    the tests would rewrite the user's real one.
    """

    path = tmp_path / "config.yaml"
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    return path


def write(path, data: dict) -> None:
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# deep_merge
# ----------------------------------------------------------------------

def test_merge_fills_in_what_the_override_omits():
    merged = deep_merge({"a": 1, "b": 2}, {"b": 3})

    assert merged == {"a": 1, "b": 3}


def test_merge_recurses_into_nested_sections():
    merged = deep_merge(
        {"voice": {"tts": {"enabled": False, "provider": "auto"}}},
        {"voice": {"tts": {"enabled": True}}},
    )

    assert merged["voice"]["tts"] == {"enabled": True, "provider": "auto"}


def test_merge_does_not_touch_sections_the_override_never_mentions():
    merged = deep_merge(
        {"voice": {"tts": {"enabled": False}}, "vision": {"enabled": False}},
        {"voice": {"tts": {"enabled": True}}},
    )

    assert merged["vision"] == {"enabled": False}


def test_a_list_replaces_rather_than_unions():
    """
    Someone who writes `allowed: [read_file]` means that list. Unioning
    it with a default would grant a permission they never wrote down.
    """

    merged = deep_merge(
        {"tools": {"allowed": ["current_time"]}},
        {"tools": {"allowed": ["read_file"]}},
    )

    assert merged["tools"]["allowed"] == ["read_file"]


def test_an_empty_list_really_means_empty():
    merged = deep_merge({"tools": {"allowed": ["a", "b"]}}, {"tools": {"allowed": []}})

    assert merged["tools"]["allowed"] == []


def test_a_scalar_can_replace_a_section():
    merged = deep_merge({"vision": {"enabled": True}}, {"vision": None})

    assert merged["vision"] is None


def test_unknown_keys_survive_the_merge():
    """An experimental section must not be silently dropped."""

    merged = deep_merge(DEFAULT_CONFIG, {"experimental": {"live2d": True}})

    assert merged["experimental"] == {"live2d": True}


def test_merging_nothing_yields_the_defaults():
    assert deep_merge(DEFAULT_CONFIG, {}) == DEFAULT_CONFIG
    assert deep_merge(DEFAULT_CONFIG, None) == DEFAULT_CONFIG


def test_merge_does_not_mutate_the_defaults():
    """
    A merge that edited DEFAULT_CONFIG in place would leak one test's
    config into the next, and one user's session into the next reload.
    """

    before = DEFAULT_CONFIG["tools"]["allowed"]

    merged = deep_merge(DEFAULT_CONFIG, {"tools": {"allowed": ["read_file"]}})
    merged["tools"]["allowed"].append("mutated")

    assert DEFAULT_CONFIG["tools"]["allowed"] == before
    assert DEFAULT_CONFIG["tools"]["allowed"] == []


# ----------------------------------------------------------------------
# Safe defaults
# ----------------------------------------------------------------------

def test_vision_is_off_by_default():
    assert DEFAULT_CONFIG["vision"]["enabled"] is False


def test_screen_pixels_are_not_captured_by_default():
    """Window titles are enough, and need no optional package."""

    assert DEFAULT_CONFIG["vision"]["capture_screen"] is False


def test_tools_are_off_and_nothing_is_allowed_by_default():
    tools = DEFAULT_CONFIG["tools"]

    assert tools["enabled"] is False
    assert tools["allowed"] == []
    assert tools["allowed_paths"] == []
    assert tools["applications"] == {}


def test_only_safe_tools_auto_approve_by_default():
    assert DEFAULT_CONFIG["tools"]["auto_approve"] == ["safe"]


def test_the_microphone_is_off_by_default():
    """No hardware is required to start."""

    assert DEFAULT_CONFIG["voice"]["stt"]["enabled"] is False


def test_speech_is_off_by_default():
    assert DEFAULT_CONFIG["voice"]["tts"]["enabled"] is False


def test_stt_defaults_to_the_mock_provider():
    """Nothing downloads a model on first run."""

    assert DEFAULT_CONFIG["voice"]["stt"]["provider"] == "mock"


def test_the_avatar_is_on_by_default():
    """It is the one optional subsystem that degrades to nothing safely."""

    assert DEFAULT_CONFIG["avatar"]["enabled"] is True


def test_every_sprint_five_section_exists():
    for section in ("voice", "vision", "avatar", "tools"):
        assert section in DEFAULT_CONFIG, section


def test_defaults_are_serialisable_as_yaml():
    """save_default_config has to be able to write this."""

    assert yaml.safe_load(yaml.dump(DEFAULT_CONFIG)) == DEFAULT_CONFIG


# ----------------------------------------------------------------------
# Defaults reaching the subsystems that read them
# ----------------------------------------------------------------------

def test_the_default_tool_policy_grants_nothing():
    policy = ToolPolicy.from_config(DEFAULT_CONFIG["tools"])

    assert policy.enabled is False
    assert policy.allowed == frozenset()


def test_a_default_install_can_run_no_tool_at_all():
    assert build_tools(DEFAULT_CONFIG["tools"]).available() == []


# ----------------------------------------------------------------------
# load_config
# ----------------------------------------------------------------------

def test_a_missing_config_is_created_from_the_defaults(config_file):
    config = load_config()

    assert config_file.exists()
    assert config["app"]["name"] == "Aura"


def test_user_settings_win(config_file):
    write(config_file, {"llm": {"temperature": 0.1}})

    config = load_config()

    assert config["llm"]["temperature"] == 0.1


def test_unspecified_keys_still_arrive(config_file):
    write(config_file, {"llm": {"temperature": 0.1}})

    config = load_config()

    assert config["llm"]["model"] == DEFAULT_CONFIG["llm"]["model"]


def test_a_sprint_four_config_still_starts(config_file):
    """
    Forward compatibility, stated as a test: a file written before voice,
    vision, avatar and tools existed must still produce a full config.
    """

    write(
        config_file,
        {
            "app": {"name": "Aura", "version": "0.1.0"},
            "llm": {"provider": "gemini", "model": "gemini-2.5-flash"},
            "memory": {"history_limit": 20},
        },
    )

    config = load_config()

    assert config["llm"]["provider"] == "gemini"
    assert config["vision"]["enabled"] is False
    assert config["tools"]["enabled"] is False
    assert config["avatar"]["enabled"] is True
    assert config["voice"]["stt"]["provider"] == "mock"


def test_an_empty_file_is_treated_as_no_overrides(config_file):
    config_file.write_text("", encoding="utf-8")

    assert load_config() == DEFAULT_CONFIG


def test_a_broken_file_falls_back_to_the_defaults(config_file):
    """Refusing to start over a stray tab in YAML helps nobody."""

    config_file.write_text("llm: [unclosed\n", encoding="utf-8")

    assert load_config() == DEFAULT_CONFIG


def test_a_non_mapping_file_falls_back_to_the_defaults(config_file):
    config_file.write_text("- just\n- a\n- list\n", encoding="utf-8")

    assert load_config() == DEFAULT_CONFIG


def test_loading_does_not_mutate_the_defaults(config_file):
    write(config_file, {"tools": {"enabled": True, "allowed": ["current_time"]}})

    load_config()

    assert DEFAULT_CONFIG["tools"]["enabled"] is False
    assert DEFAULT_CONFIG["tools"]["allowed"] == []


def test_the_written_default_file_reloads_unchanged(config_file):
    save_default_config()

    assert load_config() == DEFAULT_CONFIG


def test_the_written_file_keeps_non_ascii_readable(config_file):
    """allow_unicode=True, so Vietnamese personas stay legible on disk."""

    write(config_file, {"personality": {"name": "Aura", "note": "cà phê sữa đá"}})

    assert "cà phê sữa đá" in config_file.read_text(encoding="utf-8")
