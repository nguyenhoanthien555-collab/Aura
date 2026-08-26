"""
Regression tests for the identifier scheme (migration PART 13).

Every id is unique, every id validates, and a foreign id is rejected at
the boundary rather than accepted into circulation.
"""

from core.ids import (
    is_valid_id,
    new_id,
    new_observation_id,
    new_run_id,
    new_session_id,
    new_task_id,
    new_tool_call_id,
)


def test_ids_are_unique_across_a_thousand_mints():
    seen = {new_id("task") for _ in range(1000)}

    assert len(seen) == 1000


def test_each_kind_uses_its_own_prefix():

    assert new_task_id().startswith("task_")
    assert new_run_id().startswith("run_")
    assert new_tool_call_id().startswith("call_")
    assert new_observation_id().startswith("obs_")
    assert new_session_id().startswith("session_")


def test_all_minted_ids_validate():

    for value in (
        new_task_id(),
        new_run_id(),
        new_tool_call_id(),
        new_observation_id(),
        new_session_id(),
    ):
        assert is_valid_id(value), value


def test_foreign_identifiers_do_not_validate():

    assert not is_valid_id("")
    assert not is_valid_id(None)
    assert not is_valid_id("run_")            # no hex body
    assert not is_valid_id("RUN_ABCDEF0123456789")  # wrong case
    assert not is_valid_id("obs_zzzzzzzzzzzzzzzz")
    assert not is_valid_id("12345678901234567890")


def test_bad_prefix_is_refused_at_mint_time():
    import pytest

    with pytest.raises(ValueError):
        new_id("Task")
