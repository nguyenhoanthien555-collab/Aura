"""
Runtime configuration overrides, set from the phone.

`config.yaml` is the deployment's config and stays authoritative for
defaults. This module holds a small, validated *overlay* on top of it:
the handful of keys the Control Hub is allowed to change, persisted to
`data/settings.json`, deep-merged over the loaded config at startup.

WHY AN OVERLAY AND NOT A SECOND CONFIG SYSTEM
---------------------------------------------
Rewriting `config.yaml` from an HTTP request was the obvious alternative
and is worse in three ways: it destroys the operator's comments, it
cannot distinguish "the deployment set this" from "the user set this on
their phone", and a bad write corrupts the file the server needs to boot.
An overlay is additive - delete `data/settings.json` and the deployment is
exactly as it was.

The merge uses `core.config.deep_merge`, the same function `load_config`
already uses for `config.yaml` over `DEFAULT_CONFIG`. One merge rule, one
implementation.

WHAT MAY BE CHANGED
-------------------
`ALLOWED` is a closed allow-list of dotted paths, each with a validator.
A path that is not in it is rejected. This is deliberate: the config tree
carries things a remote client has no business setting (`server.host`,
`logging`, plugin paths), and an allow-list fails safe as the config grows
while a deny-list does not.

Two things this module does NOT do: it never holds an API key (that is
`core/credentials.py`, encrypted), and it never decides whether a change
took effect - `server/settings_service.py` owns applying changes to live
objects and reporting what needs a restart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

from core.config import deep_merge
from core.logger import logger


# Read as a module global at construction time, so a test can redirect it
# (see `tests/conftest.py`) without every caller having to pass a path.
SETTINGS_PATH = Path("data") / "settings.json"


class SettingsError(ValueError):
    """An invalid setting. The message is returned to the client verbatim."""


# ----------------------------------------------------------------------
# Validators
#
# Each returns the coerced value or raises SettingsError. They exist
# because JSON from a phone is not trustworthy: a bool arriving as the
# string "true", a cooldown of -1, a quiet-hours window of [25, 99].
# ----------------------------------------------------------------------

def _boolean(value, path: str) -> bool:

    if isinstance(value, bool):
        return value

    if isinstance(value, str) and value.strip().lower() in {
        "true", "false", "1", "0", "yes", "no",
    }:
        return value.strip().lower() in {"true", "1", "yes"}

    raise SettingsError(f"{path} must be true or false")


def _bounded_number(low: float, high: float):

    def validate(value, path: str) -> float:

        try:
            number = float(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{path} must be a number")

        if number != number or number in (float("inf"), float("-inf")):
            raise SettingsError(f"{path} must be a finite number")

        if not low <= number <= high:
            raise SettingsError(f"{path} must be between {low} and {high}")

        return number

    return validate


def _bounded_integer(low: int, high: int):

    def validate(value, path: str) -> int:

        if isinstance(value, bool):
            raise SettingsError(f"{path} must be a whole number")

        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{path} must be a whole number")

        if not low <= number <= high:
            raise SettingsError(f"{path} must be between {low} and {high}")

        return number

    return validate


def _non_empty_text(maximum: int = 120):

    def validate(value, path: str) -> str:

        if not isinstance(value, str):
            raise SettingsError(f"{path} must be text")

        text = value.strip()

        if not text:
            raise SettingsError(f"{path} cannot be empty")

        if len(text) > maximum:
            raise SettingsError(f"{path} must be at most {maximum} characters")

        return text

    return validate


def _provider_name(value, path: str) -> str:
    """
    A provider Aura can actually build.

    Checked against the router's own registry rather than a list kept
    here, so a provider added there is settable immediately and one that
    is deliberately unregistered (cerebras) is refused with a true
    reason.
    """

    from brain.router import KEYLESS_PROVIDERS, PROVIDER_KEYS

    known = {"mock", *PROVIDER_KEYS, *KEYLESS_PROVIDERS}

    name = str(value or "").strip().lower()

    if name not in known:
        raise SettingsError(
            f"{path} must be one of: {', '.join(sorted(known))}"
        )

    return name


def _provider_list(value, path: str) -> list[str]:
    """A fallback chain: known providers, no duplicates, at most six."""

    if not isinstance(value, list):
        raise SettingsError(f"{path} must be a list of provider names")

    if len(value) > 6:
        raise SettingsError(f"{path} may name at most 6 providers")

    chain: list[str] = []

    for entry in value:
        name = _provider_name(entry, path)
        if name not in chain:
            chain.append(name)

    return chain


def _quiet_hours(value, path: str) -> list[list[int]]:
    """
    A list of [start_hour, end_hour] windows, hours in 0..23.

    A window may wrap midnight ([22, 8]) - `core.temporal.in_quiet_hours`
    handles that, so equal bounds and wrapping are both legal here.
    """

    if not isinstance(value, list):
        raise SettingsError(f"{path} must be a list of [start, end] windows")

    if len(value) > 4:
        raise SettingsError(f"{path} may contain at most 4 windows")

    windows: list[list[int]] = []

    for window in value:

        if not isinstance(window, (list, tuple)) or len(window) != 2:
            raise SettingsError(
                f"{path} entries must be [start_hour, end_hour] pairs"
            )

        hours = []

        for hour in window:
            if isinstance(hour, bool):
                raise SettingsError(f"{path} hours must be whole numbers 0-23")
            try:
                number = int(hour)
            except (TypeError, ValueError):
                raise SettingsError(f"{path} hours must be whole numbers 0-23")
            if not 0 <= number <= 23:
                raise SettingsError(f"{path} hours must be between 0 and 23")
            hours.append(number)

        windows.append(hours)

    return windows


# ----------------------------------------------------------------------
# The allow-list
#
# Dotted config path -> validator. Everything the Control Hub can set is
# here and nothing else is settable. Grouped to match the UI sections so
# a screen and its permissions can be read side by side.
# ----------------------------------------------------------------------

ALLOWED: dict[str, object] = {

    # AI / Models. `model` is free text on purpose: provider model names
    # change constantly and a hardcoded enum would reject a model that
    # shipped this morning. It is length-capped and its effect is visible
    # immediately via the provider test.
    "llm.provider": _provider_name,
    "llm.model": _non_empty_text(120),
    "llm.fallback_providers": _provider_list,
    "llm.fallback_model": _non_empty_text(120),
    "llm.groq_model": _non_empty_text(120),
    "llm.mistral_model": _non_empty_text(120),
    "llm.ollama_model": _non_empty_text(120),
    "llm.temperature": _bounded_number(0.0, 2.0),
    "llm.max_output_tokens": _bounded_integer(64, 8192),
    "llm.timeout": _bounded_number(5.0, 600.0),

    # Memory. `pipeline` and `profile` gate construction and need a
    # restart; `recall` and `history_limit` are read per turn and apply
    # live. The service layer knows which is which.
    #
    # There is deliberately no `memory.enabled` here. Nothing in the
    # codebase reads such a key - `launcher/services.py` gates the stores
    # on `profile`/`recall`/`companion` and the pipeline on `pipeline` -
    # so exposing one would be a switch that changes a JSON file and
    # nothing else. A toggle has to move a subsystem or it does not exist.
    "memory.recall": _boolean,
    "memory.profile": _boolean,
    "memory.pipeline": _boolean,
    "memory.history_limit": _bounded_integer(1, 200),
    "memory.retrieval_scope": _bounded_integer(10, 5000),

    # Proactive. Phase 8 defaults are conservative and stay the defaults -
    # these bounds allow tuning, not disabling the anti-spam gate. The
    # floors are what stop a well-meaning slider from turning Aura into a
    # notification spammer: 5 minutes minimum between messages, 20 a day
    # maximum, similarity never below 0.1.
    "proactive.enabled": _boolean,
    "proactive.cooldown_seconds": _bounded_number(300.0, 86400.0),
    "proactive.max_per_day": _bounded_integer(1, 20),
    "proactive.quiet_hours": _quiet_hours,
    "proactive.duplicate_window_seconds": _bounded_number(60.0, 604800.0),
    "proactive.similarity_threshold": _bounded_number(0.1, 1.0),

    # Vision. The cloud/ollama split from Phase 8 is preserved: two keys,
    # never one, so setting the phone-facing model cannot misconfigure the
    # local processor.
    "vision.enabled": _boolean,
    "vision.cloud_model": _non_empty_text(120),
    "vision.ollama_model": _non_empty_text(120),

    # Voice. Server-side only - there is no phone TTS/STT in this app, and
    # the UI says so rather than offering a control that does nothing.
    "voice.tts.enabled": _boolean,
    "voice.stt.enabled": _boolean,

    # Tools and agent actions.
    "tools.enabled": _boolean,

    # Screen observation, the server half of the Awareness section. The
    # device half is an Android permission and is not a config key.
    "server.screen.enabled": _boolean,
    "server.companion.enabled": _boolean,
}


def validate_path(path: str, value):
    """
    Validate one dotted path. Returns the coerced value.

    Raises SettingsError for an unknown path, naming it - a client that
    sends a key this build does not know should be told which one, since
    the alternative is a silently ignored setting, which is exactly the
    "dead setting" the audit forbids.
    """

    validator = ALLOWED.get(path)

    if validator is None:
        raise SettingsError(f"{path} is not a configurable setting")

    return validator(value, path)


def flatten(tree: dict, prefix: str = "") -> dict:
    """
    A nested dict to dotted paths.

    Stops descending at any path that is itself in `ALLOWED`, so a
    structured value (`proactive.quiet_hours`, a list of lists) is passed
    to its validator whole instead of being walked into.
    """

    flat: dict = {}

    for key, value in (tree or {}).items():

        path = f"{prefix}{key}"

        if isinstance(value, dict) and path not in ALLOWED:
            flat.update(flatten(value, prefix=f"{path}."))
        else:
            flat[path] = value

    return flat


def nest(flat: dict) -> dict:
    """Dotted paths back to a nested dict, for the deep merge."""

    tree: dict = {}

    for path, value in flat.items():

        parts = path.split(".")
        cursor = tree

        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})

        cursor[parts[-1]] = value

    return tree


class RuntimeSettings:
    """
    The overlay: validated overrides, persisted, merged over the config.

    Thread-safe. A settings PATCH and a chat turn arrive on different
    request threads, and `effective()` is read by both.
    """

    def __init__(self, path: Path | str | None = None):

        self.path = Path(path) if path is not None else SETTINGS_PATH

        self._lock = RLock()
        self._overrides: dict = {}

        self._load()

    # ------------------------------------------------------------------

    def _load(self) -> None:
        """
        Read the overlay, dropping anything that no longer validates.

        A stored override can become invalid across an upgrade - a bound
        tightened, a provider unregistered. Dropping the offender and
        keeping the rest is better than refusing to start, and better than
        loading a value the current build considers out of range.
        """

        if not self.path.exists():
            return

        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning(
                "Settings overlay at %s is unreadable (%s); ignoring it",
                self.path, type(error).__name__,
            )
            return

        if not isinstance(stored, dict):
            return

        accepted: dict = {}

        for path, value in flatten(stored.get("settings") or {}).items():
            try:
                accepted[path] = validate_path(path, value)
            except SettingsError as error:
                logger.warning("Dropping stored setting: %s", error)

        with self._lock:
            self._overrides = accepted

    def _save(self) -> None:
        """Persist. Caller holds the lock."""

        self.path.parent.mkdir(parents=True, exist_ok=True)

        document = json.dumps(
            {"version": 1, "settings": nest(self._overrides)}, indent=2
        )

        temporary = self.path.with_suffix(self.path.suffix + ".tmp")

        temporary.write_text(document, encoding="utf-8")

        os.replace(temporary, self.path)

    # ------------------------------------------------------------------

    @property
    def overrides(self) -> dict:
        """The flat overlay, copied - callers must not mutate it."""

        with self._lock:
            return dict(self._overrides)

    def update(self, changes: dict) -> dict:
        """
        Validate and store `changes` (a nested or flat dict).

        All-or-nothing: every path is validated before anything is
        written, so a request that names one bad setting changes nothing.
        A partially applied settings PATCH is the kind of state nobody can
        reason about afterwards.

        Returns the accepted flat paths and their coerced values.
        """

        flat = flatten(changes)

        if not flat:
            raise SettingsError("No settings were provided")

        accepted = {
            path: validate_path(path, value) for path, value in flat.items()
        }

        with self._lock:
            previous = dict(self._overrides)
            self._overrides.update(accepted)

            try:
                self._save()
            except Exception as error:
                self._overrides = previous
                logger.error(
                    "Settings could not be persisted (%s)", type(error).__name__
                )
                raise SettingsError(
                    "Settings could not be saved on the server"
                ) from error

        return accepted

    def reset(self, paths: list[str] | None = None) -> list[str]:
        """
        Drop overrides, reverting to what config.yaml says.

        With no paths, drops everything. Returns what was actually
        removed.
        """

        with self._lock:

            if paths is None:
                removed = sorted(self._overrides)
                self._overrides = {}
            else:
                removed = [p for p in paths if p in self._overrides]
                for path in removed:
                    self._overrides.pop(path, None)

            if removed:
                self._save()

        return removed

    def effective(self, config: dict) -> dict:
        """
        `config` with the overlay merged over it.

        The base is not mutated: `deep_merge` writes into its first
        argument, so a copy is merged and returned. The runtime holds one
        config dict for the process lifetime and a caller asking "what is
        effective" must not silently rewrite it.
        """

        import copy

        merged = copy.deepcopy(config or {})

        with self._lock:
            overlay = nest(dict(self._overrides))

        return deep_merge(merged, overlay)


_settings: RuntimeSettings | None = None


def peek_runtime_settings() -> RuntimeSettings | None:
    """
    The overlay, without building it.

    `core.config.load_config` calls this on its boot path. Building a
    store there - touching the disk, reading `data/settings.json` during
    import - would be a side effect in the wrong place and could deadlock
    a test that installed a store mid-import. None means "no overlay
    active", which is exactly right for a process that never sets one.
    """

    return _settings


def get_runtime_settings() -> RuntimeSettings:
    """The process-wide settings overlay."""

    global _settings

    if _settings is None:
        _settings = RuntimeSettings()

    return _settings


def set_runtime_settings(store: RuntimeSettings | None) -> None:
    """Install an overlay (tests), or reset to lazy construction with None."""

    global _settings

    _settings = store


