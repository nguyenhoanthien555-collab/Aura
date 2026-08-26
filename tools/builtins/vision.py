"""
Looking at the screen on request.

Vision has two shapes in Aura and this module is the second one.

The first is ambient: `VisionManager` is handed to the conversation as a
`brain.ports.VisionProvider`, and every turn quietly carries a line
saying what is on screen. That path is throttled, silent on failure, and
never asked for - it is context, not an action.

This is the other shape. The model decides it needs to look, says so, and
gets an answer it can reason about. Section 22 of the modernization
directive puts that behind the same tool boundary as everything else:
a name, a description, a risk level, an `execute()` and a `verify()`,
running through `ToolExecutor` with its gates rather than beside them.

Three things are deliberate:

- **It reuses the manager rather than capturing its own frames.** The
  capture backend, the processor chain, the source label and the event
  publishing already exist and are already tested. A tool that grabbed
  its own screenshot would be a second vision implementation whose
  behaviour drifted from the one in the prompt - and section 41 says not
  to build that.

- **It respects `vision.enabled`.** `VisionManager.refresh()` does not
  check that flag; only `get_context()` does. So a tool calling refresh
  blindly would look at the owner's screen after the owner had switched
  looking off, which is section 2's "must NOT silently override owner
  configuration" with pixels attached. The check is made twice: the
  factory does not register the tool when vision is off, and `execute`
  refuses if it was switched off since.

- **Its risk level is read off the processor chain, not fixed.** Naming
  the screen is SENSITIVE - it reads the owner's data. Uploading a
  picture of it to a hosted model is a different act, so when the chain
  can do that the same tool is DANGEROUS instead. See `_risk_for` below.

It takes no parameters. The manager is bound to one capture at build
time, and a `monitor` argument would only be able to lie about which
display it changed.
"""

from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok


# How old the manager's observation may be and still count as "looking
# now". Generous on purpose: `VisionManager.refresh` stamps its clock
# *before* asking the processor, so a vision model that takes twenty
# seconds to answer leaves a twenty second old observation behind and
# has done nothing wrong. This bound is not a latency policy. It exists
# to catch a description served out of an old cache - a tool that says
# "the screen shows X" having never looked.
STALE_AFTER = 60.0


def _risk_for(processor) -> ToolRisk:
    """
    How risky looking is, given where the pixels can end up.

    SENSITIVE reads the owner's data and keeps it on the machine: a
    window title, or a local model over loopback. DANGEROUS is the
    honest label once a hosted provider is in the chain, because then
    the act is not "read the screen" but "send a picture of the screen
    to a third party", and section 30 does not let that ride on the same
    permission as reading a file.

    Read through `getattr` with a False default, the way the tool layer
    reads every optional member: `sends_pixels_offsite` is a fact a
    processor may advertise, not something the VisionProcessor protocol
    demands, so a processor written before this existed still works and
    is simply taken at its quieter word.

    Over-reporting is the safe direction. A chain whose cloud link has
    no usable key will never upload anything and still reports
    DANGEROUS; the cost of that is one confirmation prompt, and the cost
    of the mistake in the other direction is an upload nobody approved.
    """

    if getattr(processor, "sends_pixels_offsite", False):
        return ToolRisk.DANGEROUS

    return ToolRisk.SENSITIVE


class DescribeScreenTool(Tool):

    name = "describe_screen"

    description = (
        "Look at the screen right now and say what is on it. Use this "
        "when the answer depends on what the user is currently seeing "
        "and the conversation has not already said. The description "
        "will include whatever is visible, including anything private"
    )

    # Replaced per instance in __init__. The class value is the floor,
    # not the answer: an instance built around a chain that can upload
    # reports DANGEROUS. ToolRegistry.register reads `tool.risk` off the
    # instance, and the approval gate reads it again per call, so the
    # per-instance value is the one that decides anything.
    risk = ToolRisk.SENSITIVE

    parameters: tuple[Parameter, ...] = ()

    def __init__(self, vision):
        """
        `vision` is a VisionManager. It is required rather than optional
        with a default, because there is no sensible one to build here:
        the manager in this process is already wired to a capture
        backend, a processor chain, an event bus and a clock, and a
        second one built locally would observe a different screen with
        different settings.
        """

        self.vision = vision

        self.risk = _risk_for(getattr(vision, "processor", None))

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(self) -> str:
        """
        Observe now and return the description.

        `refresh()` rather than `get_context()`: the throttle exists so
        that fifty turns in a minute do not become fifty screenshots,
        and it is exactly wrong here. The model asked to look. Handing
        it a two second old cache would answer a different question than
        the one it asked.
        """

        if not self.vision.is_available():
            raise RuntimeError(
                "vision is switched off - turn on vision.enabled to let "
                "Aura look at the screen"
            )

        context = self.vision.refresh()

        if context is None or context.is_empty():
            raise RuntimeError(
                "the screen could not be described: no window title to "
                "read and no vision model answered"
            )

        return context.render()

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify(self) -> ToolResult | None:
        """
        Section 11: not "it did not throw".

        Two independent readings of the manager's state, neither of them
        the return value of `execute`:

          * something is being held, and it is not empty - so the
            description handed to the model is one the manager actually
            recorded, rather than one produced and then dropped;

          * that observation was taken just now - so the description
            came from looking, not from a cache that happened to be
            lying around.

        The freshness half is the one that matters. It is the only check
        that can tell "I looked and saw X" from "I did not look and X is
        what I remember", and those are the same string.

        Deliberately *not* compared against what `execute` returned. That
        would need the tool to remember its last answer across two calls,
        and the manager is shared - an ambient observation landing between
        execute and verify would change the held description for a
        perfectly good reason and fail a call that did nothing wrong.

        `last_observation` and `seconds_since_observation` are read rather
        than `get_context()`, which re-observes once its throttle expires.
        With `min_interval: 0` that would mean a second capture, and with
        a hosted provider in the chain a second upload - verification
        paying the cost of the thing it verifies.
        """

        age = getattr(self.vision, "seconds_since_observation", None)

        if age is None:
            return fail(
                "the screen was described but nothing was observed",
                tool=self.name,
            )

        if age > STALE_AFTER:
            return fail(
                f"the description came from an observation {age:.0f}s "
                f"old rather than from looking now",
                tool=self.name,
            )

        held = getattr(self.vision, "last_observation", None)

        if held is None:
            return fail(
                "the observation was not retained, so the description "
                "cannot be confirmed",
                tool=self.name,
            )

        if held.is_empty():
            return fail(
                "the retained observation describes nothing",
                tool=self.name,
            )

        return ok(
            f"observed {age:.1f}s ago and still held: {held.render()}",
            tool=self.name,
        )
