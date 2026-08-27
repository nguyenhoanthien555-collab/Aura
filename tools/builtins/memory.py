"""
Memory tools.

`remember` is the semantic tier's way in from a conversation.

`MemoryPipeline.remember_user_stated` existed, was tested, and was
called by nothing outside the test suite, so no matter what the user
told Aura she could not keep a durable keyed fact about them - the
episodic tier saved the sentence, and the thing the sentence *meant*
went nowhere. This is the caller.

Why a tool the model asks for, rather than a pass over every message:

The keys are namespaced - `identity.name`, not `name` - and turning
"I'm Thien btw" into that key is a judgement, not a pattern. Regex
extraction would invent keys or miss most facts, and an extra LLM call
per message to do it properly costs a call per message for facts that
appear in maybe one.

More importantly, `memory/user_model.py` is explicit that CONFIRMED
means the user actually said it, and that `confirm()` is the only door
in. A tool call is that: the model read the message, chose the key, and
asked. A background scraper inferring intent is not, and would quietly
fill the confirmed tier with guesses - which is the one thing the user
model's own docstring forbids.
"""

from memory.user_model import CATEGORIES, IDENTITY
from tools.base import Parameter, Tool, ToolRisk, fail, ok


class RememberTool(Tool):
    """
    Store one durable fact about the user.

    SAFE, and the reasoning is worth writing down because it is arguable.
    The taxonomy in `tools/base.py` grades damage outside Aura: SAFE
    reads a clock, SENSITIVE moves the user's data somewhere it was not,
    DANGEROUS changes the machine. This sends nothing outward, touches
    nothing on disk but Aura's own database, and files something the user
    said moments ago in the process that already had it.

    The counter-argument - it writes, and the write persists - is real.
    What settles it is the cost of being wrong in each direction. Too
    loose, and a bad call puts a wrong fact in the user's profile, which
    `forget` undoes. Too strict, and `auto_approve: [safe]` plus no human
    to ask in server mode refuses every call, so the tier is unreachable
    again with a permission error standing in for a design decision.
    """

    name = "remember"

    description = (
        "Store a durable fact about the user so it outlives this "
        "conversation - who they are, what they prefer, what they are "
        "working on. Not for what merely happened; that is saved anyway."
    )

    risk = ToolRisk.SAFE
    capability = 'memory.write'

    # Inline, not on the executor's worker thread, and this is required
    # rather than an optimisation.
    #
    # `call_with_timeout` runs a tool on a daemon thread so a hung call
    # cannot stop the conversation. A SQLAlchemy SQLite session belongs to
    # the thread that opened it - touching it from another raises
    # ProgrammingError - so a threaded `remember` fails every time, in
    # production exactly as in tests. `tools/timeout.py` names this case
    # in its own docstring: a timeout of 0 or less "runs the call inline
    # on this thread", for a tool that "loses the ability to touch
    # thread-affine state" otherwise.
    #
    # The deadline is what is given up. That is affordable here and would
    # not be for a network tool: this writes a handful of rows to a local
    # file that Aura is the only writer of, and the thread the deadline
    # needs could not have killed the write anyway.
    timeout = 0

    parameters = (
        Parameter(
            name="key",
            description=(
                "Namespaced key naming the fact, e.g. identity.name, "
                "communication.language, project.current. Reuse the same "
                "key to update a fact rather than adding a second one."
            ),
        ),
        Parameter(
            name="value",
            description="The fact itself, in the user's own words where possible",
        ),
        Parameter(
            name="category",
            description="One of: " + ", ".join(sorted(CATEGORIES)),
            required=False,
        ),
    )

    def __init__(self, pipeline):
        """
        `pipeline` is a MemoryPipeline. Injected, like every other tool's
        dependency, so this class never reaches for a global session.
        """

        self.pipeline = pipeline

    def execute(
        self,
        key: str = "",
        value: str = "",
        category: str = IDENTITY,
    ):
        # Every argument here was chosen by a language model, so each one
        # is checked rather than trusted. Refusals return `fail` with a
        # reason the model can act on: the result goes back to it as TOOL
        # RESULTS, and a refusal it can understand becomes a corrected
        # second attempt instead of a dropped fact.

        key = (key or "").strip()
        value = (value or "").strip()
        category = (category or IDENTITY).strip().lower()

        if not key:
            return fail(
                "a key is required, e.g. identity.name", tool=self.name
            )

        if not value:
            # A stored blank reads like a fact whose answer is "nothing",
            # and `value_of` cannot tell it from a real one.
            return fail(
                f"no value given for {key!r} - nothing to remember",
                tool=self.name,
            )

        if category not in CATEGORIES:
            return fail(
                f"unknown category {category!r}. Use one of: "
                + ", ".join(sorted(CATEGORIES)),
                tool=self.name,
            )

        stored = self.pipeline.remember_user_stated(
            key, value, category=category
        )

        # Section 11: not "the write did not raise" but "the fact is
        # there". `remember_user_stated` returns the Belief it wrote, or
        # None when it wrote nothing - and it writes nothing when the key
        # normalises to an empty slug. `normalise_key` keeps only [a-z0-9],
        # so a key a model reasonably chooses - "名前", "?", a bare
        # emoji - strips to "" and stores silently. Reporting "remembered"
        # for that write puts a fact into the model's reply, and then in
        # front of the user, that their profile does not contain.
        #
        # The reason names the key so the model's next attempt can pick one
        # that survives, rather than re-sending the same unusable one.
        if stored is None:
            return fail(
                f"could not remember {key!r}: it has no letters or digits "
                f"to key on, so nothing was stored. Try a key like "
                f"identity.name.",
                tool=self.name,
            )

        return ok(f"remembered {key} = {value}", tool=self.name)

    def verify(self, key: str = "", value: str = "", category: str = IDENTITY):
        """
        The postcondition of remembering: the fact reads back.

        Consulted by the executor after a successful `execute`, through
        the same `value_of` the prompt recalls with - so this proves the
        fact is reachable the way it will actually be reached, not merely
        that a row exists. `value_of` normalises the key itself, so the raw
        key the model supplied is the right thing to ask with.

        A blank key is not this method's failure to report. `execute`
        already refused it with its own reason, so `verify` never runs for
        one, and returning None here keeps that refusal the single voice on
        the matter rather than adding a second.
        """

        key = (key or "").strip()

        if not key:
            return None

        stored = self.pipeline.user_model.value_of(key)

        if not stored:
            return fail(
                f"{key} was not stored - it could not be read back after "
                f"remembering.",
                tool=self.name,
            )

        return ok(f"{key} reads back as {stored!r}", tool=self.name)
