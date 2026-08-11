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
