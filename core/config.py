"""
Aura configuration.

`load_config()` returns DEFAULT_CONFIG deep merged with config.yaml, so
every key a subsystem reads is guaranteed to exist. That guarantee is
what lets a user's older config.yaml survive a new sprint: sections
added here appear automatically, and anything the user set wins.

Defaults are chosen to be safe rather than impressive. Vision is off,
tools are off and nothing is allowed, the avatar is on but degrades to
nothing when there is no display. A fresh Aura talks, and does not
watch, click or launch anything until asked to.
"""

import copy

import yaml

from core.paths import CONFIG_PATH

__all__ = [
    "CONFIG_PATH",
    "DEFAULT_CONFIG",
    "load_config",
    "save_default_config",
    "deep_merge",
]


DEFAULT_CONFIG = {
    "app": {
        "name": "Aura",
        "version": "0.2.0",
    },

    "llm": {
        "provider": "mock",
        "model": "gemini-2.5-flash",
        "temperature": 0.7,
        "max_output_tokens": 4096,
    },

    "memory": {
        "history_limit": 20,

        # Long term recall. Profile facts are cheap and always useful;
        # keyword recall is off until the transcript is long enough to
        # be worth searching.
        "profile": True,
        "recall": False,
        "max_facts": 8,
        "max_recalled": 3,
    },

    "voice": {
        "tts": {
            "enabled": False,

            # "auto" picks the best voice this machine already has:
            # SAPI on Windows, pyttsx3 if installed, otherwise a silent
            # mock. It never installs or downloads anything.
            "provider": "auto",
            "voice": "",
            "rate": 0,
            "volume": 100,
        },

        "stt": {
            "enabled": False,
            "provider": "mock",     # "mock" | "whisper"
            "model": "base",
            "language": "",
            "record_seconds": 5.0,

            # Wake word gating for continuous listening. Ignored by
            # push to talk, which is explicit by definition.
            "wake_word": "",
        },

        "microphone": {
            "sample_rate": 16000,
            "channels": 1,
            "device": None,
        },
    },

    "vision": {
        # Off by default. Reading someone's screen is opt in, always.
        "enabled": False,

        # Seconds between observations. Turns arrive far faster than the
        # screen meaningfully changes.
        "min_interval": 2.0,

        # Window titles only. Turn this on to also grab pixels, which
        # needs the optional `mss` package.
        "capture_screen": False,
        "monitor": 1,
    },

    "avatar": {
        "enabled": True,
        "size": 160,
        "scale": 1.0,
        "opacity": 0.95,

        # null = bottom right of the primary display
        "position": None,

        # Drop idle/listening/thinking/speaking PNGs here to replace the
        # placeholder shape.
        "sprites_dir": "",
    },

    "tools": {
        # Two locks. Both must be opened: the system has to be enabled,
        # and each tool has to be named in `allowed`.
        "enabled": False,
        "allowed": [],

        # Risk levels that run without asking. Anything not listed here
        # needs a live confirmation, and with no confirmation handler
        # attached it simply cannot run.
        "auto_approve": ["safe"],

        # Directories read_file and list_directory may touch. Empty
        # means those tools are not even registered.
        "allowed_paths": [],

        # Nickname to executable. The only programs Aura can launch.
        "applications": {},
    },

    "logging": {
        "level": "INFO",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """
    Merge `override` onto a copy of `base`, recursing into dicts.

    Only dict-into-dict recurses. A list or scalar in `override`
    replaces the default outright, because a user who writes
    `allowed: [read_file]` means exactly that list - silently unioning
    it with a default would grant permissions they did not write down.
    """

    result = copy.deepcopy(base)

    for key, value in (override or {}).items():

        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)

        else:
            result[key] = value

    return result


def load_config() -> dict:
    """
    Load config.yaml, filling in anything it does not specify.

    A missing file is created from the defaults. A malformed one is
    reported and the defaults are used, because refusing to start over a
    stray tab in YAML helps nobody.
    """

    if not CONFIG_PATH.exists():
        save_default_config()

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

    except (OSError, yaml.YAMLError) as error:
        # core.logger imports nothing from here, but keeping this import
        # local avoids a cycle if that ever changes.
        from core.logger import logger

        logger.warning("Could not read config.yaml (%s), using defaults", error)
        config = None

    if not isinstance(config, dict):
        config = {}

    return deep_merge(DEFAULT_CONFIG, config)


def save_default_config():
    """
    Write a fresh config.yaml.
    """

    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        yaml.dump(
            DEFAULT_CONFIG,
            file,
            allow_unicode=True,
            sort_keys=False,
        )
