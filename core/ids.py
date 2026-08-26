"""
Identifiers.

Every agent task, run, tool call and observation carries an id minted
here, and the prefixes are the contract the rest of the migration reads:

    task_   one user request that may take several runs
    run_    one execution of the agent loop over a task
    call_   one tool invocation inside a run
    obs_    one observation captured by a device or a provider
    msg_    one message persisted in a conversation

The reason these exist is the stale-state family of bugs: a response from
one task reused by another, a screenshot from one step shown as the next
step's current screen, a tick from an old run answering a new request.
Those are all possible only when the things being confused have no
identity to begin with. With ids on everything, "is this result from the
run I am running?" becomes a comparison instead of a hope.

Format: `<prefix>_<16 hex chars>` - enough entropy that ids never collide
in practice, short enough to read in a log line, and checkable with
`is_valid_id` at any boundary that needs to reject a foreign id rather
than silently accept it.
"""

import re
import uuid

# prefix + "_" + 16 lowercase hex characters.
ID_PATTERN = re.compile(r"^[a-z]+_[0-9a-f]{16}$")

_ID_LENGTH = 16


def new_id(prefix: str) -> str:
    """One fresh identifier with the given prefix."""

    if not re.fullmatch(r"[a-z]+", prefix or ""):
        raise ValueError(
            f"id prefix must be lowercase letters, got {prefix!r}"
        )

    return f"{prefix}_{uuid.uuid4().hex[:_ID_LENGTH]}"


def new_task_id() -> str:
    return new_id("task")


def new_run_id() -> str:
    return new_id("run")


def new_tool_call_id() -> str:
    return new_id("call")


def new_observation_id() -> str:
    return new_id("obs")


def new_session_id() -> str:
    return new_id("session")


def is_valid_id(value: object) -> bool:
    """
    True when `value` looks like an id this module mints.

    Used at boundaries - a route accepting a run id from a device, a store
    accepting an observation id - so a malformed identifier is rejected
    where it arrived instead of failing later in a lookup that cannot
    explain itself.
    """

    return isinstance(value, str) and bool(ID_PATTERN.fullmatch(value))
