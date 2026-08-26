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
import uuid
from pathlib import Path
from threading import RLock

from core.config import deep_merge
from core.logger import logger
from core.temporal import canonical_timezone_name, resolve_timezone


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


def _lane_provider(value, path: str) -> str:
    """
    A provider for one task lane, or "" to retire the lane.

    Empty is accepted where `llm.provider` refuses it, and that asymmetry
    is the point: `llm.provider` is what Aura falls back to, so it must
    always name something, while a lane is optional by construction.
    Clearing one has to be expressible, or the owner could add a lane
    through the settings API and then never remove it.
    """

    if value is None:
        return ""

    if isinstance(value, str) and not value.strip():
        return ""

    return _provider_name(value, path)


def _clearable_text(maximum: int = 120):
    """
    Free text, or "" to mean "not configured".

    `_non_empty_text` is right for a setting that must always name
    something - every vendor model name does. It is wrong for one that
    only exists while a custom endpoint does, because refusing "" would
    leave the owner unable to undo having set it.
    """

    inner = _non_empty_text(maximum)

    def validate(value, path: str) -> str:

        if value is None:
            return ""

        if isinstance(value, str) and not value.strip():
            return ""

        return inner(value, path)

    return validate


def _timezone_name(value, path: str) -> str:
    """
    A zone this machine can actually resolve, or "" for its own clock.

    Empty is accepted for the same reason `llm.custom_base_url` accepts
    it: a zone set once has to be retirable, and "" is both what
    `core/config.py` ships and what means "use this machine's clock".

    An unresolvable name is refused rather than stored, which is the
    opposite of what `resolve_timezone` does at startup - and the
    asymmetry is the point. Refusing at startup would mean no Aura at all
    over a typo, so it logs and carries on with the host clock. Refusing
    here costs the owner one error message and leaves the value they
    already had standing, whereas *storing* it would leave the settings
    screen showing a zone the clock never uses: exactly the dead setting
    `validate_path` refuses by name. Nothing is silently rewritten either
    way, which is what section 2 asks of this file.

    The message names `tzdata` because on Windows an unresolvable name is
    usually not a typo at all - the zone is real and the database is
    missing - and an error that only said "unknown timezone" would send
    the owner hunting for a spelling mistake that is not there.
    """

    if value is None:
        return ""

    if not isinstance(value, str):
        raise SettingsError(f"{path} must be text")

    name = canonical_timezone_name(value)

    if not name:
        return ""

    if len(name) > 60:
        raise SettingsError(f"{path} must be at most 60 characters")

    if resolve_timezone(name) is None:
        raise SettingsError(
            f"{path}: this machine cannot resolve the timezone {name!r}. "
            f"IANA names need a system timezone database - on Windows, "
            f"`pip install tzdata` provides one. UTC always works."
        )

    return name


def _endpoint_url(value, path: str) -> str:
    """
    An http(s) endpoint the owner supplies, or "" to retire it.

    The scheme is required rather than assumed. Prepending one here would
    mean choosing between http and https on the owner's behalf, and
    choosing wrong sends their API key over the wire in cleartext - so a
    bare hostname is refused with a message saying so instead.

    Nothing else about the URL is judged. A loopback address is a
    first-class case (vLLM, llama.cpp, LM Studio and LiteLLM all live
    there), a private host may be the only place the gateway exists, and a
    port or a path may be anything. Validating beyond the scheme would be
    this file deciding which of the owner's own machines are acceptable.
    """

    if value is None:
        return ""

    if not isinstance(value, str):
        raise SettingsError(f"{path} must be text")

    url = value.strip().rstrip("/")

    if not url:
        return ""

    if len(url) > 400:
        raise SettingsError(f"{path} must be at most 400 characters")

    if not url.startswith(("http://", "https://")):
        raise SettingsError(
            f"{path} must start with http:// or https:// - the scheme is "
            f"not assumed, because guessing it wrong would send your key "
            f"unencrypted"
        )

    if "://" not in url or not url.split("://", 1)[1]:
        raise SettingsError(f"{path} must include a host")

    return url


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


def _risk_levels(value, path: str) -> list[str]:
    """
    Which tool risk levels skip the confirmation prompt.

    The vocabulary is `ToolRisk` in `tools/base.py`, not a list invented
    here - a level this codebase does not define would be dropped
    silently by `ToolPolicy.from_config`, leaving a setting that appears
    to have been accepted and does nothing.

    An empty list is refused for the same reason, and it is the subtle
    one: `from_config` reads `config.get("auto_approve") or ["safe"]`, so
    `[]` does not mean "confirm everything" - it collapses back to
    auto-approving safe tools. Accepting it would store a value the
    running policy contradicts. "Confirm everything" is not expressible
    through this key, so it is refused rather than mistranslated.
    """

    if not isinstance(value, list):
        raise SettingsError(
            f"{path} must be a list of risk levels (safe, sensitive, dangerous)"
        )

    from tools.base import ToolRisk

    known = {level.value for level in ToolRisk}

    out: list[str] = []

    for entry in value:
        name = str(entry or "").strip().lower()

        if name not in known:
            raise SettingsError(
                f"{path}: {name or 'empty'} is not a risk level "
                f"({', '.join(sorted(known))})"
            )

        if name not in out:
            out.append(name)

    if not out:
        raise SettingsError(
            f"{path} needs at least one risk level "
            f"({', '.join(sorted(known))})"
        )

    return out


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
    "llm.openai_model": _non_empty_text(120),
    "llm.anthropic_model": _non_empty_text(120),
    "llm.cerebras_model": _non_empty_text(120),
    "llm.xai_model": _non_empty_text(120),
    "llm.deepseek_model": _non_empty_text(120),
    "llm.qwen_model": _non_empty_text(120),
    # The owner's own endpoint. Both are clearable, unlike every model
    # above them: a vendor model name always names something, while a
    # custom endpoint has to be retirable or an owner who tries one can
    # never go back to a vendor cleanly.
    "llm.custom_base_url": _endpoint_url,
    "llm.custom_model": _clearable_text(120),
    # One key per lane rather than a single nested dict, so an unknown
    # task name is refused by name instead of being stored and ignored.
    "llm.task_models.reasoning": _lane_provider,
    "llm.task_models.coding": _lane_provider,
    "llm.task_models.tool_planning": _lane_provider,
    "llm.task_models.fast_response": _lane_provider,
    "llm.task_models.long_context": _lane_provider,

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
    #
    # `capture_screen` and `min_interval` were readable in the effective
    # document and settable nowhere, which is the section 2 failure in its
    # plainest form: config.yaml has honoured both since phase 8, so the
    # owner could change them by editing a file on the server and not
    # through the application's own settings. Nothing about either needed
    # to be built to make them settable - they were simply missing from
    # this table.
    #
    # `send_screen_to_cloud` is new in phase 19 and is the one key here
    # that decides whether pixels leave the machine. It defaults to false
    # and is listed so the owner can turn it on deliberately; see
    # `launcher/services.py::_build_cloud_vision` for why a provider key
    # in the environment is not by itself an answer to that question.
    #
    # `min_interval` is deliberately *not* wired to a live applier, so it
    # reports restart_required. `_reapply_screen` already assigns
    # `services.vision.min_interval` from `server.screen.min_interval`,
    # and the two sections address the same live object - the desktop
    # manager and the device-fed manager are never both running. A second
    # writer would give this key a value the other one silently reverts,
    # which is the failure the `voice.rate`/`voice.pitch` note above calls
    # worse than saying "restart".
    #
    # `monitor`, `host`, `timeout` and `debug_frame` stay absent. Which
    # display, which daemon, how long to wait and where to dump frames are
    # deployment facts of the machine Aura runs on, and a phone that could
    # retarget the vision host over the network would be pointing the
    # owner's screen at an endpoint the owner never typed on that machine.
    "vision.enabled": _boolean,
    "vision.capture_screen": _boolean,
    "vision.send_screen_to_cloud": _boolean,
    "vision.min_interval": _bounded_number(0.0, 3600.0),
    "vision.cloud_model": _non_empty_text(120),
    "vision.ollama_model": _non_empty_text(120),

    # Voice. Server-side only - there is no phone TTS/STT in this app, and
    # the UI says so rather than offering a control that does nothing.
    #
    # `voice`/`volume` are read at each synthesis (`EdgeTTSProvider.speak`
    # passes `self.voice`/`self.volume` through), so they can be moved on a
    # running provider. `provider` and `playback` cannot: one selects the
    # class, the other is `create_audio_player(enabled=...)` at build time.
    #
    # `rate` and `pitch` are deliberately absent. The mood pacing system
    # owns them at runtime and restores from `_base_rate`, so a value set
    # here would be silently reverted by the next mood change - a setting
    # that un-sets itself is worse than one that says "restart".
    "voice.tts.enabled": _boolean,
    "voice.tts.provider": _non_empty_text(40),
    "voice.tts.voice": _non_empty_text(80),
    "voice.tts.volume": _bounded_integer(0, 100),
    "voice.tts.playback": _boolean,
    "voice.stt.enabled": _boolean,

    # Tools and agent actions. All three are `ToolPolicy` fields, and the
    # executor reads every one of them per call (`tools/executor.py` lines
    # 119-275), so replacing the policy applies them without a restart.
    #
    # `auto_approve` is the confirmation gate: a level *not* listed makes
    # every tool of that risk wait for a human. It cannot be emptied - see
    # `_risk_levels` for why an empty list would not mean what it looks
    # like.
    #
    # Deliberately NOT here: `tools.allowed`, `tools.allowed_paths`,
    # `tools.applications` and `tools.commands`. Those four decide which
    # tools exist and which filesystem roots, executables and commands they
    # may touch - granting a new capability, not configuring an existing
    # one. A bearer token is enough to change a setting; it is not enough to
    # hand a remote client a new verb on the host.
    #
    # `tools.commands` is the sharpest version of that rule and the reason
    # it is worth restating rather than assuming. A settable `commands`
    # would let anything holding the token declare `["cmd", "/c", "{x}"]`
    # and then fill in `{x}` - which is precisely the arbitrary shell
    # execution Section 24 forbids, arrived at through the settings API
    # instead of through the tool. The owner declares commands by editing
    # config on the machine that will run them.
    "tools.enabled": _boolean,
    "tools.auto_approve": _risk_levels,
    "tools.timeout": _bounded_number(1.0, 300.0),

    # Screen observation, the server half of the Awareness section. The
    # device half is an Android permission and is not a config key.
    #
    # `min_interval` is live: `VisionManager._is_fresh` reads the
    # attribute on every observation, so setting it moves the next one.
    "server.screen.enabled": _boolean,
    "server.screen.min_interval": _bounded_number(1.0, 300.0),

    # The companion gate: the six knobs that decide how talkative Aura is
    # when nobody asked her anything. Only `enabled` was reachable until
    # phase 14, which left an owner who found her chatty with one control -
    # off - and no way to say "less".
    #
    # The bounds are the same shape as `proactive.*` above and for the same
    # reason: they allow tuning, not the removal of the anti-spam gate
    # (section 20). Five minutes is the shortest cooldown, which is what
    # makes twelve the highest reachable hourly ceiling - a larger number
    # would be one the cooldown never lets anybody reach, and a setting
    # that cannot take effect is worse than one that is not offered.
    #
    # `suppress_after_chat_seconds` floors at 0 rather than at a positive
    # number: an owner who wants to be interrupted mid-conversation is
    # asking for less silence from their own assistant, and section 2 says
    # that is theirs to decide. Every other floor here protects the owner
    # from noise; this one would only protect them from quiet.
    "server.companion.enabled": _boolean,
    "server.companion.relevance_threshold": _bounded_number(0.1, 1.0),
    "server.companion.cooldown_seconds": _bounded_number(300.0, 86400.0),
    "server.companion.max_per_hour": _bounded_integer(1, 12),
    "server.companion.quiet_hours": _quiet_hours,
    "server.companion.suppress_after_chat_seconds": _bounded_number(0.0, 3600.0),
    "server.companion.duplicate_window_seconds": _bounded_number(60.0, 604800.0),

    # What time Aura thinks it is. Read by `TemporalClock.from_config`,
    # and settable because the deployment the key was written for is
    # exactly the one that cannot edit config.yaml: a container running in
    # UTC whose owner is not. `effective` has always reported this value
    # to the phone; until it was here the phone could only read it, which
    # made being confidently an hour out the only reachable state.
    "temporal.timezone": _timezone_name,
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

        # Random identity for this store instance. `core.config.load_config`
        # caches its merge and keys the cache partly on this token plus the
        # version counter below, so a Hub settings change is visible to the
        # very next `load_config()` without that function re-reading
        # config.yaml - and a swapped-in store is never mistaken for its
        # predecessor (a plain id() could be reused after garbage
        # collection).
        self._cache_token = uuid.uuid4()

        # Bumped on every accepted change.
        self._version = 0

        self._load()

    # ------------------------------------------------------------------

    @property
    def cache_token(self) -> uuid.UUID:
        """Identity of this store instance; cache key for load_config."""

        return self._cache_token

    @property
    def version(self) -> int:
        """Bumped on every accepted change; cache key for load_config."""

        with self._lock:
            return self._version

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

            # Only after a successful persist: load_config keys its merge
            # cache on this counter, so it must mean "this store's
            # overrides are now these".
            self._version += 1

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
                # Same reasoning as in `update`: the cache key must move
                # only when the overrides actually moved.
                self._version += 1

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


