"""
Machine turns.

Not every turn through ConversationManager is a conversation. The Android
accessibility agent sends a screen and asks what to do next; the answer is
a JSON object a service parses and executes. Before that, the app asks one
short question - "is this something to do, or something to say?" - and the
answer is a single word.

Neither is speech. Both are read by a parser, so both have to skip
everything that exists to make Aura sound like herself: personality,
response style, the identity anchor, the transcript, and the memory the
next real turn is built from. A JSON action saved as an assistant message
gets quoted back to the model on the following turn as though Aura had
said it out loud.

The predicate lives here, on its own, because three callers have to agree
on it and previously did not:

    PromptBuilder         which sections the prompt gets
    ConversationManager   whether the reply is styled, saved, announced
    the tests             that hold those two together

Detection is by context key, never by message text. The Android service
sends the literal message "agent_tick", but that string is a label for a
log line, not a contract - the context is what carries the screen.
"""

import re

from brain.recovery import absorbed_failure


# A snapshot of the device is present, so this turn is one step of the
# agent loop. Either key alone is enough: a tick taken before the tree
# could be serialised still carries the device.
AGENT_TICK_KEYS = ("accessibility_tree", "device")

# Set by a client asking only how the next message should be routed.
INTENT_PROBE_KEY = "intent_probe"


ACTION = "action"

CONVERSATION = "conversation"


def is_agent_tick(context: dict | None) -> bool:
    """
    True when this turn is one step of the device agent.

    The single definition. Anything that needs to tell an agent step from
    a conversation imports this rather than re-deriving it, because two
    copies of the rule that drifted apart is exactly how a JSON action
    ended up in the conversation transcript.
    """

    if not isinstance(context, dict):
        return False

    return any(key in context for key in AGENT_TICK_KEYS)


def is_intent_probe(context: dict | None) -> bool:
    """True when this turn only asks how a message should be routed."""

    if not isinstance(context, dict):
        return False

    return bool(context.get(INTENT_PROBE_KEY))


def is_machine_turn(context: dict | None) -> bool:
    """True for any turn whose reply is parsed rather than read."""

    return is_agent_tick(context) or is_intent_probe(context)


def read_intent(reply: str | None) -> str:
    """
    The routing decision carried by a classifier reply.

    Conservative on purpose, because the two mistakes do not cost the
    same. Sending a conversation into the agent loop spends a screen
    capture and up to ten silent steps on a message that only wanted an
    answer. Sending an action into conversation costs one sentence
    saying nothing was done, and the user can rephrase.

    So anything unclear - an empty reply, both words, neither word,
    a provider that failed - is CONVERSATION.
    """

    text = (reply or "").strip().lower()

    if not text:
        return CONVERSATION

    words = set(re.findall(r"[a-z]+", text))

    if ACTION in words and CONVERSATION not in words:
        return ACTION

    return CONVERSATION


# What `AuraAccessibilityService.formatActionHistory` emits, and nothing
# more. The Kotlin side builds every entry as `kind(args) [VERIFIED]`:
#
#   open_app(com.google.android.youtube) [VERIFIED]
#   click(search_button) [VERIFIED]
#   input_text(search_box, "Minecraft") [VERIFIED]
#   home() [VERIFIED]
#
# pinned by `AccessibilityAgentTest`. The trailing marker is not optional
# on the device - the list is only appended to on the verified-success
# branch - so it is not matched here either. A line that does not fit is
# skipped rather than guessed at: the history is prose assembled for a
# prompt, and half-reading it would put a fact in the cognitive state
# that no device ever reported.
_VERIFIED_ACTION = re.compile(r"^\s*(\w+)\s*\((.*)\)\s*\[VERIFIED\]\s*$")

# The same signature, with the device's other two `ExecutionResult` names
# and the count it already keeps:
#
#   open_app(com.android.chrome) [FAILED x2]
#   click(node_12) [UNVERIFIED x1]
#
# `AuraAccessibilityService.formatActionFailure` builds these from the same
# `actionSignature` that builds the verified lines, so the two formats
# cannot drift apart into different notions of what identifies an action.
#
# The verdicts are the device's own vocabulary rather than a taxonomy
# invented here. FAILED means the gesture could not be performed;
# UNVERIFIED means it was performed and its postcondition was not observed
# - which is section 11's distinction, and the half that would be lost if
# failure arrived as a single "it did not work".
#
# The count travels on the line because the device already keeps it in
# `failedActionsCount`. The alternative - one line per attempt - would make
# the server's arithmetic depend on how many ticks happened to have passed,
# and every tick re-sends the whole list.
_FAILED_ACTION = re.compile(
    r"^\s*(\w+)\s*\((.*)\)\s*\[(FAILED|UNVERIFIED)\s+x(\d+)\]\s*$"
)


def _target_of(arguments: str) -> str:
    """
    The part of an action's arguments that says what was acted on.

    The first argument, and split on the first comma so a quoted string
    containing one cannot shift the boundary. `input_text` also carries the
    text it typed, and that is deliberately dropped - the same box typed
    into twice is one step of the task, and a retry with corrected text is
    still that step, so including the text would split one action into two
    records and defeat the "have I already done this?" lookup.

    One implementation for both readers on purpose. A success and a failure
    that disagreed about an action's identity would be two records for one
    action, and the count on one of them would never reach its bound.
    """

    return arguments.split(",", 1)[0].strip().strip('"').strip("'")


def read_action_history(entries) -> list[tuple[str, str]]:
    """
    Turn a tick's completed-action lines into `(kind, target)` pairs.

    Unparseable lines are absent from the result rather than represented
    as an unknown action. Callers get only what the device actually said.
    """

    pairs: list[tuple[str, str]] = []

    for entry in entries or ():
        match = _VERIFIED_ACTION.match(str(entry))

        if not match:
            continue

        pairs.append((match.group(1), _target_of(match.group(2).strip())))

    return pairs


def read_action_failures(entries) -> list[tuple[str, str, str, int]]:
    """
    Turn a tick's failed-action lines into `(kind, target, verdict, count)`.

    The sibling of `read_action_history`, and the reason the server can tell
    a launch that failed twice from one nobody has tried. Before this, the
    only failure signal was `last_action_error` - free prose in five shapes,
    none of which reliably names the action it refers to - so the server
    could not mark a node failed, could not know an attempt had been spent,
    and could not enter recovery. All three producers existed; none had an
    input.

    A verified line is not a failure and is not read as one, and neither is
    a line missing its count. Half-reading it would put a fact in the
    cognitive state that no device ever reported, which is the same bargain
    `read_action_history` makes.
    """

    failures: list[tuple[str, str, str, int]] = []

    for entry in entries or ():
        match = _FAILED_ACTION.match(str(entry))

        if not match:
            continue

        failures.append((
            match.group(1),
            _target_of(match.group(2).strip()),
            match.group(3).upper(),
            int(match.group(4)),
        ))

    return failures


def absorb(state, context: dict | None) -> bool:
    """
    Record what an agent tick reported. True when anything changed.

    The tick is where reality arrives - the foreground app, the screen,
    the request, and what the device has already done - and until now it
    was read once to render a prompt and then dropped. That left the phone
    as the only thing that remembered progress, telling the model in prose:
    `AuraAccessibilityService` sends "This action was already successfully
    executed. Do not repeat it." as an error string, which works exactly as
    well as the model's willingness to believe it.

    Writing it into the session's `CognitiveState` instead makes "have I
    already opened YouTube?" a lookup. This function is the whole ingest;
    it lives beside `is_agent_tick` because this module is already the one
    place allowed to know how a tick is shaped, and two readers of that
    shape drifting apart is the bug it was created to prevent.

    A turn that is not a tick is left alone. Nothing here decides policy -
    it records what the device said happened, in both directions. Successes
    land as succeeded because the device reports the verified branch;
    failures land as failed with their attempt count brought up to the
    number the device reported, which is bookkeeping rather than an
    intention to act. How many attempts are allowed, and whether an action
    may be tried again, belong to `brain/recovery.py`.

    Successes are read before failures, and the order is load-bearing. The
    device clears an action's failure count when it finally verifies, but a
    tick already in flight can still carry the old line; reading the failure
    last would let a stale count reopen work that has since succeeded.

    `last_action_error` is deliberately not absorbed. The device builds it
    as free prose in five different shapes, none of which reliably names
    the action it refers to, and deriving a `(kind, target)` from it would
    be inventing a format rather than reading one. `failed_actions` exists
    because it is the same format `completed_actions` already uses, defined
    on both sides at once - which is a different thing from inventing an
    interpretation of free text after the fact.
    """

    if not is_agent_tick(context):
        return False

    changed = False

    app = context.get("app") or {}

    if isinstance(app, dict) and (app.get("package") or app.get("activity")):
        changed |= state.observe(
            application=app.get("package") or None,
            screen=app.get("activity") or None,
        )

    request = context.get("user_request")

    if request and state.goal != request:
        state.set_goal(request)
        changed = True

    history = (
        context.get("completed_actions")
        or context.get("action_history")
        or []
    )

    for kind, target in read_action_history(history):
        # Already recorded, so nothing to do. Skipping rather than
        # re-recording is what keeps attempt counts honest: every tick
        # re-sends the whole history, and counting each replay would
        # exhaust a retry bound on step two through repetition of the
        # report alone.
        if state.has_succeeded(kind, target):
            continue

        state.succeed_action(kind, target)
        changed = True

    for kind, target, verdict, count in read_action_failures(
        context.get("failed_actions")
    ):
        changed |= absorbed_failure(state, kind, target, verdict, count)

    return changed
