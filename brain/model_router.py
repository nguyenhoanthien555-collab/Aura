"""
Model Router 2.0: one lane per task class, one AURA above all of them.

`BrainRouter` resolves a single provider chain and serves every request
through it. That is still exactly what the owner gets by default, and
this module does not replace it - it holds several of them, one per task
class, and picks between them.

Three properties are load-bearing, and each one is a thing that must NOT
happen:

- The owner's provider setting is never rewritten. A lane is additive
  configuration read alongside `llm.provider`; the chat lane *is*
  `llm.provider` unless the owner said otherwise. Every lane is a
  `BrainRouter`, so the existing "log at ERROR, change nothing" behaviour
  when a provider is dead is inherited rather than reimplemented.

- A broken lane cannot take AURA down. An optional lane that fails to
  build, or fails mid-answer, degrades to the chat lane. This is the same
  rule that stops a dead primary provider from killing the app, applied
  one level up. The chat lane is the floor: when *it* fails the error
  propagates, because a silent empty reply looks to the user like AURA
  had nothing to say.

- Nothing above the router can tell which lane answered. The prompt bytes
  are built before this module is reached and are passed through
  untouched, so persona, identity, transcript and style are identical
  whichever worker replies.

The public surface is `BrainRouter`'s, because five modules duck-type it:
`generate`, `provider_name` (readable *and* writable), `active_chain()`,
and an assignable `_provider`. `server/settings_service.py` reapplies a
provider change by writing the first and clearing the last, and that code
needs no knowledge of lanes.
"""

from core.logger import logger

from brain.capabilities import TaskClass


class CapabilityRouter:
    """
    A lane per task class, each one a provider chain in its own right.

    Lanes are built on first use, never at construction. A router that
    reached out for six providers when it was created would make importing
    AURA depend on the network, and would build workers for task classes a
    given session never uses.
    """

    def __init__(self, chat=None, lanes=None, config=None):
        """
        `chat` and each lane value may be a provider name or a live LLM.

        A name is resolved through `BrainRouter`, which already knows how
        to find a key, assemble a fallback chain and explain a provider it
        had to skip. A live object is used as given, which is what makes
        this testable without a network and what lets the composition root
        hand over an already-built router.
        """

        # Before anything else: `_provider` is a property whose setter
        # touches `_built`, and `__getattr__` reads `_configured_chat`.
        self._built: dict[TaskClass, object] = {}
        self._configured_chat = chat
        self._lanes: dict[TaskClass, object] = {}

        for task, spec in (lanes or {}).items():
            if spec:
                self._lanes[TaskClass.coerce(task)] = spec

        if config is not None:
            self._absorb_config(config)

        # A lane that names the same provider as chat is not a lane. It
        # would build a second identical chain, doubling the providers
        # held open for no behavioural difference.
        self._lanes.pop(TaskClass.CHAT, None)

    def _absorb_config(self, config: dict) -> None:
        """
        Read `llm.task_models` without ever writing configuration.

        Unknown task names are dropped with a warning rather than raising:
        a settings file written by a newer build should cost the owner a
        default lane, not a server that will not start.
        """

        llm = config.get("llm") or {}
        declared = llm.get("task_models") or {}

        if not isinstance(declared, dict):
            logger.warning(
                "llm.task_models is %s, not a mapping - ignoring it",
                type(declared).__name__,
            )
            return

        for name, provider in declared.items():
            if not provider:
                continue
            task = TaskClass.coerce(name)
            if task is TaskClass.CHAT and str(name).strip().lower() != "chat":
                logger.warning(
                    "llm.task_models names an unknown task %r - ignoring it", name
                )
                continue
            self._lanes[task] = provider

        if self._configured_chat is None and llm.get("provider"):
            self._configured_chat = llm["provider"]

    # ------------------------------------------------------------------
    # Lane construction
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(spec):
        """A live LLM for `spec`, which may be a provider name."""

        if isinstance(spec, str):
            from brain.router import BrainRouter

            return BrainRouter(provider_name=spec)

        return spec

    def _chat_lane(self):
        """
        The lane every other lane degrades to.

        Built from `llm.provider` when the owner configured nothing else,
        which is why a `CapabilityRouter` with no lanes at all behaves
        exactly like the `BrainRouter` it replaced.
        """

        lane = self._built.get(TaskClass.CHAT)

        if lane is None:
            spec = self._configured_chat
            if spec is None:
                from brain.router import BrainRouter

                lane = BrainRouter()
            else:
                lane = self._resolve(spec)
            self._built[TaskClass.CHAT] = lane

        return lane

    def _lane(self, task: TaskClass):
        """
        The lane for `task`, or None when there is nothing configured.

        A lane that cannot be built at all is reported once and then
        treated as absent, so a broken optional lane costs one warning
        rather than one per turn.
        """

        if task in self._built:
            return self._built[task]

        spec = self._lanes.get(task)
        if spec is None:
            return None

        try:
            lane = self._resolve(spec)
        except Exception as error:
            logger.error(
                "The %s lane could not be built (%s: %s). Aura is using the "
                "chat lane for %s instead. No setting has been changed.",
                task.value, type(error).__name__, error, task.value,
            )
            self._lanes.pop(task, None)
            return None

        self._built[task] = lane
        return lane

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(self, prompt: str) -> str:
        """
        Answer as the chat lane.

        This is what makes a `CapabilityRouter` a drop-in `LLM`: anything
        that was handed a `BrainRouter` and calls `generate` keeps working
        and gets the provider the owner configured.
        """

        return self._chat_lane().generate(prompt)

    def generate_for(self, prompt: str, task=None) -> str:
        """
        Answer as the lane for `task`, degrading to chat.

        The prompt is passed through byte-for-byte. Nothing here inspects
        or rewrites it, which is the mechanical reason a lane cannot
        change AURA's identity.
        """

        resolved = TaskClass.coerce(task) if task is not None else TaskClass.CHAT

        if resolved is not TaskClass.CHAT:
            lane = self._lane(resolved)
            if lane is not None:
                try:
                    return lane.generate(prompt)
                except Exception as error:
                    logger.warning(
                        "The %s lane failed (%s: %s). Falling back to the "
                        "chat lane for this turn.",
                        resolved.value, type(error).__name__, error,
                    )

        return self._chat_lane().generate(prompt)

    def __getattr__(self, name):
        """
        Expose `stream` only when the chat lane can actually stream.

        `brain.streaming.can_stream` asks whether the attribute exists, so
        a plainly-defined `stream` method would claim the capability on
        behalf of providers that do not have it, and the caller would
        discover the truth only after committing to a streaming response.

        Underscored names are refused outright: this hook runs during
        `__init__` for any attribute not yet assigned, and reaching
        `_chat_lane` from there would build a provider mid-construction.
        """

        if name.startswith("_"):
            raise AttributeError(name)

        if name == "stream":
            target = getattr(self._chat_lane(), "stream", None)
            if callable(target):
                return target

        raise AttributeError(name)

    # ------------------------------------------------------------------
    # The BrainRouter surface that five other modules duck-type
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        """What was asked for, not what was obtained."""

        spec = self._configured_chat

        if isinstance(spec, str):
            return spec

        if spec is None:
            return getattr(self._chat_lane(), "provider_name", "unknown")

        return getattr(spec, "provider_name", type(spec).__name__)

    @provider_name.setter
    def provider_name(self, value: str) -> None:
        """
        Record an owner-driven provider change and drop the stale lane.

        `server/settings_service.py` writes this and then clears
        `_provider`; honouring the write here is what lets that code stay
        unaware that lanes exist.
        """

        self._configured_chat = value
        self._built.pop(TaskClass.CHAT, None)

    @property
    def _provider(self):
        return self._built.get(TaskClass.CHAT)

    @_provider.setter
    def _provider(self, value) -> None:
        """
        Assigning None invalidates every lane, not just chat.

        A provider change usually means a credential changed, and a lane
        that failed for want of that credential deserves another attempt.
        """

        if value is None:
            self._built.clear()
        else:
            self._built[TaskClass.CHAT] = value

    def active_chain(self) -> str:
        """
        The chain that answers ordinary conversation.

        Deliberately the chat lane alone: this is read by health checks
        that ask "can AURA reply", and naming six lanes there would turn
        one answer into a report.
        """

        lane = self._chat_lane()
        described = getattr(lane, "active_chain", None)

        if callable(described):
            try:
                return described()
            except Exception:
                pass

        return getattr(lane, "provider_name", self.provider_name)

    def describe_lanes(self) -> dict:
        """
        Which provider serves each task class, for diagnostics.

        Reports configuration rather than built objects wherever it can,
        so asking the question does not build a provider as a side effect.
        """

        described = {TaskClass.CHAT: self.provider_name}

        for task, spec in self._lanes.items():
            if isinstance(spec, str):
                described[task] = spec
            else:
                described[task] = getattr(spec, "provider_name", type(spec).__name__)

        return described
