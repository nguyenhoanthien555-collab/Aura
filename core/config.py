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
import threading

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

        # The fallback chain, in order. This is the authoritative
        # setting; `fallback_provider` below is its superseded singular
        # form, still read when the list is empty so an older config.yaml
        # does not silently lose its failover.
        "fallback_providers": [],
        "fallback_provider": "",

        # Per-provider models. `model` above names the *primary* cloud
        # model and is not a valid tag for any of these, so each provider
        # that needs a different one says so here rather than having it
        # inferred from the primary's name.
        "fallback_model": "",
        "groq_model": "llama-3.3-70b-versatile",
        "mistral_model": "mistral-small-latest",
        "ollama_model": "qwen3:8b",

        # The Phase 11 providers. Each of these must equal its provider
        # class's `default_model`, or the Control Hub would show one model
        # and the request would carry another; the agreement is asserted in
        # `tests/test_cloud_providers.py`. All are free text on purpose - a
        # hardcoded list here would reject a model released this morning.
        "openai_model": "gpt-5.1",
        "anthropic_model": "claude-sonnet-5",
        "cerebras_model": "llama-3.3-70b",
        "xai_model": "grok-4",
        "deepseek_model": "deepseek-chat",
        "qwen_model": "qwen-plus",

        # The owner's own endpoint: a gateway, a proxy, or a model server
        # on their own machine. Empty on purpose, all three of them - a
        # placeholder URL would resolve, answer 404, and read exactly like
        # an outage at a provider they never configured. `brain/router.py`
        # skips `custom` until the owner has filled these in, and names
        # which one is still empty.
        "custom_base_url": "",
        "custom_model": "",

        # Which provider answers which kind of question. Empty means
        # "whatever `provider` above says", which is the default and what
        # every install had before lanes existed - so an owner who
        # configures nothing here notices no difference at all.
        #
        # A lane is additive: it never rewrites `provider`, and a lane
        # that is dead or misconfigured degrades back to it rather than
        # failing the turn. Keys are the task classes `classify_task` can
        # actually return (brain/capabilities.py). `vision`, `embedding`
        # and `fallback` are task classes too, but nothing routes to them
        # yet and a setting nothing reads is worse than no setting.
        "task_models": {
            "reasoning": "",
            "coding": "",
            "tool_planning": "",
            "fast_response": "",
            "long_context": "",
        },

        "temperature": 0.7,
        "max_output_tokens": 768,

        # How much hidden reasoning a thinking model may do before it
        # starts writing the answer. Gemini 3 charges those thoughts
        # against `max_output_tokens`, so on the default budget an
        # ordinary question spent ~700 tokens thinking and ~60 answering,
        # and the reply arrived cut off mid-sentence with no error
        # anywhere. "low" restores the meaning this file has always
        # assumed: `max_output_tokens` is the length of the *reply*.
        #
        # Values: "low" | "high" | "" (send nothing - the model's own
        # default). Raising it to "high" is a legitimate choice, but
        # raise `max_output_tokens` with it or the answer is truncated
        # again. Read only by providers whose API has such a knob.
        "thinking_level": "low",
    },

    "memory": {
        "history_limit": 20,

        # Long term recall. Profile facts are cheap and always useful;
        # recall is off until the transcript is long enough to be worth
        # searching.
        #
        # `recall` gates *both* recall mechanisms: the Sprint 5 keyword
        # search over the transcript (`launcher/services.py` picks
        # `KeywordRetriever` or `NullRetriever`) and the Memory 2.0
        # ranked episodic search (`MemoryPipeline.recall_enabled`). It
        # only reached the first until Phase 11. The phone presents it as
        # "Use memory in replies - look things up from past
        # conversations", under privacy, so it has to mean both.
        "profile": True,
        "recall": False,
        "max_facts": 8,
        "max_recalled": 3,

        # Companion memory: session-only context about the user (facts,
        # preferences, goals, projects, coding style, highlights). On by
        # default, in-memory only, no database.
        "companion": True,
        "max_companion": 10,
        "max_highlights": 3,

        # Memory 2.0: the episodic store, the temporary context and the
        # structured user model, behind one pipeline. On by default -
        # without it Aura keeps the transcript and the older profile
        # recall, which is Sprint 5 behaviour.
        "pipeline": True,

        # Write the bundled profile into the user model at startup.
        # Idempotent, and it never overwrites a confirmed entry or undoes
        # a correction, so leaving it on costs one query per entry per
        # restart. Turn it off to start from an empty model.
        "seed_profile": True,

        # How much of the transcript ranked recall may consider. A
        # ceiling, not a target: the ranker still returns `max_recalled`.
        "retrieval_scope": 500,

        # Semantic recall (AURA 2.0 Phase 2). OFF by default, and off is
        # a complete configuration: with no embedding provider, memory
        # behaves exactly as it did before this block existed - lexical
        # retrieval only, nothing sent anywhere, nothing to fail.
        #
        # `provider` picks the embedding source:
        #   hashing  local, dependency-free, deterministic n-gram
        #            hashing. Generalizes token overlap a little; it is
        #            NOT paraphrase understanding (documented in
        #            memory/embeddings.py).
        #   ollama   local model server (base_url, model). Nothing
        #            leaves the machine.
        #   remote   an OpenAI-compatible /embeddings endpoint. Sends
        #            memory content OFF the machine, so it is inert
        #            until `allow_remote` is explicitly true - the
        #            exfiltration boundary, enforced in the provider.
        # Changing provider or model marks existing vectors stale;
        # semantic retrieval stays unavailable until a reindex runs
        # (MemoryPipeline.semantic_indexer.reindex()).
        "semantic": {
            "enabled": False,
            "provider": "hashing",
            "model": "",
            "base_url": "",
            "api_key_env": "",
            "allow_remote": False,
            "top_k": 6,
            # Semantic's share of the fused hybrid score; lexical takes
            # the rest. 0.5 is an equal split, which orders results
            # exactly as unweighted fusion does. Raise it to trust
            # paraphrase more, lower it to trust literal wording more.
            "weight": 0.5,
            # Cosine floor below which a semantic match counts as
            # noise. null means "use the provider's own measured
            # floor" - the right default, because the useful cutoff
            # depends on the embedding space (see
            # `recommended_min_similarity` in memory/embeddings.py and
            # the sweep in scripts/benchmark_semantic.py). Set a number
            # only when a sweep on YOUR provider justifies it.
            "min_similarity": None,
            "timeout": 5.0,
            "batch_size": 32,
        },
    },

    # What time Aura thinks it is.
    #
    # Empty means this machine's timezone, which is right for a desktop
    # and wrong for a container in another region - a server deployment
    # should set it to the user's zone, not the host's. Named zones need
    # the `tzdata` package on Windows; UTC never does.
    "temporal": {
        "timezone": "",
    },

    # The claim -> evidence boundary on the finished reply (Phase 4).
    #
    #   enabled  whether replies are checked against the evidence this
    #            turn actually gathered - tool outcomes, recalled memory
    #            lines, live capability state.
    #   repair   whether an unsupported claim is rewritten, or only
    #            counted. False is observe-only: identical text out,
    #            full diagnostics, so this layer can be measured against
    #            real traffic before it is allowed to change anything.
    #
    # On by default, which is a deliberate departure from how Phase 2's
    # semantic memory shipped. The difference is what the switch costs.
    # Semantic memory off meant "no embeddings leave the machine"; this
    # is local, deterministic, sub-millisecond and sends nothing
    # anywhere, and its failure mode is that the reply passes through
    # untouched. What it prevents - Aura telling you it sent a message
    # that no tool ever sent - is a correctness bug, not a feature, so
    # the default is the one that fixes it.
    #
    # It only ever makes a reply say LESS. Nothing here can add a fact,
    # raise a confidence, or invent a reason; every rule can only
    # downgrade an assertion to a hedge or replace it with what the
    # evidence actually said.
    "response": {
        "verify": {
            "enabled": True,
            "repair": True,
        },
    },

    # Speaking first.
    #
    # Off by default, and that is the correct default for something that
    # can interrupt: it is turned on deliberately, per deployment, by
    # someone who wants it. Every value below is a ceiling on how often
    # Aura may speak unprompted, and the defaults are conservative
    # enough to be dull.
    "proactive": {
        "enabled": False,

        # Nothing unprompted within two hours of anything else
        # unprompted, whatever the category.
        "cooldown_seconds": 7200,

        # And at most this many in one calendar day.
        "max_per_day": 4,

        # Per-category cooldowns, in seconds. A greeting is a
        # once-a-part-of-day thing; an appreciation should be rare
        # enough to mean something.
        "category_cooldowns": {
            "greeting": 21600,
            "appreciation": 86400,
            "wellbeing": 43200,
            "task": 14400,
        },

        # Hours Aura stays quiet, as [start, end] pairs that may wrap
        # midnight. 22:00 to 08:00 by default.
        "quiet_hours": [[22, 8]],

        # Don't repeat a message within this window...
        "duplicate_window_seconds": 21600,

        # ...and don't send a near-miss of one either. Jaccard overlap
        # above this counts as the same message.
        "similarity_threshold": 0.6,
    },

    "personality": {
        # Who Aura is lives in prompts/personality.md, not here. This
        # section only controls the style layer that sits on top of it.
        "style": {
            "enabled": True,

            # Delete assistant boilerplate the model emitted out of
            # habit: "Certainly!", "I apologize for the inconvenience",
            # "Is there anything else I can help you with?".
            #
            # Subtractive only. It never rewrites a sentence and never
            # touches anything inside code, so it cannot change what a
            # reply says - only how much throat clearing surrounds it.
            "strip_filler": True,

            # Override the instruction added near the end of the prompt.
            # Empty uses brain.style.DEFAULT_HINT.
            "hint": "",

            # How many recent opening phrases to remember, so she is
            # told not to reuse them. 0 disables the tracking.
            "avoid_repeats": 3,
        },

        # Character consistency over a long conversation.
        #
        # A personality described once at the top of the prompt gets
        # further away with every turn, and the model starts answering
        # like the transcript it has been reading rather than like Aura.
        # This restates who she is *after* the transcript, where recency
        # makes it stick.
        #
        # Prompt construction only. Nothing inspects or rewrites a reply.
        "consistency": {
            "enabled": True,

            # Messages - not turns - before the reminder appears at all.
            # A three exchange conversation has not drifted, so it costs
            # zero tokens until it might have.
            "after_messages": 6,

            # Messages before the "stay consistent with what you already
            # said" clause is added. Held back until there is enough
            # transcript to actually contradict.
            "contradiction_after": 20,

            # Override the identity line. Empty uses
            # brain.consistency.IDENTITY. The drift and contradiction
            # clauses are not configurable - they are the guard itself,
            # not flavour.
            "anchor": "",
        },

        # The per-turn persona contract: which pronoun register this
        # conversation is in, which context mode this message calls for,
        # and how far the humour/brainrot dials are turned. Derived from
        # the conversation by brain/persona.py, never stored, so a
        # provider fallback is handed the same person the primary would
        # have been.
        "persona": {
            "enabled": True,

            # Pin one pronoun pair instead of deriving it from the
            # conversation. Empty derives it - the shipped behaviour.
            # Valid: "tui_bro", "cau_to", "minh_ban", "sparse".
            "pronoun_style": "",

            # Optional ceilings on the humour/brainrot dials, 0.0-1.0.
            # Each mode sets its own dials; a value here caps the dial
            # in every mode. Omit both for no cap. A "brainrot: 0.0"
            # means brainrot off everywhere, including EXCITED mode.
            # "humor": "",
            # "brainrot": "",
        },
    },

    "voice": {
        "tts": {
            "enabled": False,

            # "auto" picks the best voice this machine already has:
            # SAPI on Windows, pyttsx3 if installed, otherwise a silent
            # mock. It never installs or downloads anything.
            #
            # "edge" is the one worth switching to on purpose. It is
            # Aura's intended voice - warm, expressive, conversational -
            # but it needs `pip install edge-tts` and a network round
            # trip per reply, so it is opt in rather than automatic.
            "provider": "auto",

            # Empty means "whatever the provider considers its own
            # voice". For Edge that is zh-CN-XiaoxiaoNeural.
            "voice": "",

            # One scale shared by every provider, written the way Edge
            # wants it. SAPI and pyttsx3 read the number out of it, so
            # switching providers never needs this rewritten.
            #
            # Slightly quicker and slightly brighter than neutral: it
            # reads as someone talking rather than narrating.
            "rate": "+5%",
            "pitch": "+10Hz",
            "volume": 100,

            # Seconds to wait for Edge to return audio. This is a network
            # round trip, so past this something is wrong and waiting
            # only delays the fallback to a silent reply.
            "timeout": 60.0,

            # Seconds before a playback process is assumed stuck. A guard
            # against a hung player, not an expected speech length.
            "playback_timeout": 300.0,

            # Set false to synthesise without playing - useful when
            # something downstream owns the audio, and the reason the
            # player is injected rather than constructed inside the
            # provider.
            "playback": True,
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
        # needs the optional `mss` and `pillow` packages.
        #
        # `monitor` is an mss index: 1 is the primary display, 2 the
        # second, and 0 the union of all of them. Getting this wrong is
        # the difference between describing the screen the user is
        # looking at and describing a different one.
        "capture_screen": False,
        "monitor": 1,

        # Two model keys, because two processors read this section and
        # want different kinds of name. `cloud_model` is a hosted model
        # name for vision/cloud_processor.py (server mode);
        # `ollama_model` is an Ollama tag posted to a local daemon when
        # capture_screen is on. `vision.settings` resolves both, and
        # still honours a legacy `vision.model` written before the split.
        # Empty host falls back to llm.host.
        "cloud_model": "",
        "ollama_model": "qwen2.5vl:7b",
        "host": "",
        "timeout": 120.0,

        # Write the exact frame handed to the model here, as PNG, for
        # verifying what it actually saw. Empty writes nothing. This is a
        # screenshot on disk, so it stays off unless asked for.
        "debug_frame": "",
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

        # Seconds a single tool call may take before Aura stops waiting.
        # The wait is bounded, not the tool: a hung call is abandoned
        # rather than killed, because killing a thread mid-write leaves
        # locks held and files half written. 0 removes the bound.
        "timeout": 30.0,

        # Directories read_file and list_directory may touch. Empty
        # means those tools are not even registered.
        "allowed_paths": [],

        # Directories write_file, append_to_file, create_directory and
        # delete_file may touch. A SEPARATE list from allowed_paths, not
        # a default from it: reading a directory and being allowed to
        # overwrite it are two different grants, and section 2 says the
        # owner must not find the second one already made. Empty means
        # those four tools are not registered.
        "writable_paths": [],

        # Nickname to executable. The only programs Aura can launch.
        "applications": {},

        # Name to argv. The only commands Aura can run, and the only
        # arguments she may fill in. Empty means `run_command` is not
        # registered, which is a clearer answer to the model than a tool
        # that refuses every name it is given.
        "commands": {},
    },

    "plugins": {
        # Two locks, the same shape tools use. A plugin has to be
        # discovered, and its name has to appear here.
        #
        # A list enables exactly those names. `true` enables everything
        # found, which is convenient while developing a plugin and a bad
        # idea afterwards. An empty list is a deliberate "none", which is
        # different from having never configured this at all.
        "enabled": [],

        # An extra directory to search, alongside plugins/builtins.
        # Empty means only bundled plugins are considered.
        "directory": "",

        # Per plugin settings, keyed by plugin name. Each plugin receives
        # only its own entry, never this whole map.
        #
        #   config:
        #     session_stats:
        #       format: "%d turns in %s"
        "config": {},
    },

    "logging": {
        "level": "INFO",
    },

    "server": {
        # Read only by server mode (`launcher.py --server`). Desktop mode
        # never looks at this section.
        #
        # No secret belongs here. config.yaml is committed; the bearer
        # token, CORS origins, host and port come from the environment
        # (`AURA_SERVER_*`). See .env.example and docs/SECURITY.md.

        # Seconds an idle chat session is kept before it is swept.
        "session_ttl_seconds": 3600,

        # Screen observation reported by a mobile client. Off until the
        # user turns it on - here *and* on the device. Two switches, the
        # same shape tools and plugins use.
        "screen": {
            "enabled": False,

            # Seconds between accepted observations. Below this an
            # arriving frame is dropped, not queued.
            "min_interval": 8.0,

            # How different a screen has to be from the last one before
            # it counts as a change worth thinking about, 0.0 to 1.0.
            "min_change_ratio": 0.25,
        },

        # Unprompted messages back to the device.
        #
        # Every default here exists to keep Aura quiet. She notifies when
        # she has something worth saying, not when something happened.
        "companion": {
            "enabled": False,

            # Confidence a thought must reach before it is allowed out.
            "relevance_threshold": 0.7,

            # Seconds after any notification before the next one may fire.
            "cooldown_seconds": 300,

            # A hard ceiling that survives a bad relevance score.
            "max_per_hour": 6,

            # Local hour ranges to stay silent through, e.g. [[23, 7]].
            "quiet_hours": [],

            # Seconds after the user's last message during which nothing
            # unprompted is sent - she is already in the conversation.
            "suppress_after_chat_seconds": 120,

            # How long a remark counts as "already said". Named here rather
            # than left a module constant so the owner can reach it, the
            # way they already can on the proactive side.
            "duplicate_window_seconds": 1800,
        },
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


_cache_lock = threading.Lock()

# The last merged config and the key it was built from. The key is
# (config.yaml stat, overlay token+version); a mismatch of any part
# forces a full re-merge. See `load_config`.
_cached_config: dict | None = None
_cache_key: tuple | None = None


def load_config() -> dict:
    """
    Load config.yaml, filling in anything it does not specify.

    A missing file is created from the defaults. A malformed one is
    reported and the defaults are used, because refusing to start over a
    stray tab in YAML helps nobody.

    Three layers, lowest first: DEFAULT_CONFIG, config.yaml, and the
    runtime overlay set from the Control Hub (`core/settings_store.py`).
    The overlay is applied *here* rather than at the call sites because
    several subsystems - `GeminiProvider.__init__`, `OllamaProvider`,
    `vision/settings.py` - call this function directly and would
    otherwise never see a setting the user had changed. One merge point
    means no dead settings.

    The merged result is cached; a full re-merge happens only when
    something could have changed: config.yaml was rewritten (its stat
    moved), the overlay's overrides changed (its version counter moved),
    or a different - or no - overlay is installed (its token moved).
    Callers always receive a fresh deep copy, so nobody can mutate the
    cache out from under the next caller - the same isolation
    re-reading everything gave them.
    """

    global _cached_config, _cache_key

    if not CONFIG_PATH.exists():
        save_default_config()

    try:
        stat = CONFIG_PATH.stat()
        stamp: tuple | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None

    # Overlay identity must come from the same source apply_overlay uses,
    # but lazily and guarded exactly like apply_overlay does: importing
    # settings_store here must not break configuration loading.
    try:
        from core.settings_store import peek_runtime_settings

        overlay = peek_runtime_settings()
        if overlay is None:
            overlay_key: tuple = ("none",)
        else:
            overlay_key = (
                # A random per-store token rather than id(): the singleton
                # is swapped by plain attribute assignment all over the
                # tests, and CPython may hand a freed store's id to its
                # replacement - which would let one store's cached merge
                # be served for another.
                overlay.cache_token,
                overlay.version,
            )
    except Exception:
        overlay_key = ("none",)

    key = (stamp, overlay_key)

    with _cache_lock:
        if _cached_config is not None and _cache_key == key:
            return copy.deepcopy(_cached_config)

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

    merged = deep_merge(DEFAULT_CONFIG, config)
    merged = apply_overlay(merged)

    with _cache_lock:
        _cache_key = key
        _cached_config = merged

    return copy.deepcopy(merged)


def apply_overlay(config: dict) -> dict:
    """
    Merge the runtime overlay over `config`, if one is loaded.

    Imported lazily and guarded: `core.settings_store` imports this
    module for `deep_merge`, and its validators import `brain.router`,
    which imports this module again. A module-level import would be a
    cycle; a failure here must not break configuration loading, which is
    on the boot path for everything.

    Deliberately reads the overlay only when one has already been
    constructed - `load_config` runs during import of several modules and
    must not itself build a store, touch the disk, or decide that an
    absent overlay file should be created.
    """

    try:
        from core.settings_store import peek_runtime_settings
    except Exception:
        return config

    overlay = peek_runtime_settings()

    if overlay is None:
        return config

    try:
        return overlay.effective(config)
    except Exception as error:
        from core.logger import logger

        logger.warning(
            "Runtime settings overlay could not be applied (%s)",
            type(error).__name__,
        )
        return config


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
