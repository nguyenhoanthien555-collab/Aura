"""
Regression tests for the observation engine (migration PART 5, 13, 24).

The properties under test are the ones whose absence produced the
stale-screen and stale-context bugs: identity on every observation,
honest freshness, hashes that change when content changes, and per-run
scoping so one run cannot read another's screen.
"""

import time

from core.ids import is_valid_id
from core.observations import (
    DEFAULT_FRESH_WINDOW_S,
    Observation,
    ObservationKind,
    ObservationStore,
    StaleObservationError,
    hash_payload,
)


class TickingClock:
    """Deterministic time, advanced explicitly by the tests."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_store(clock=None):
    return ObservationStore(clock=clock or TickingClock())


def test_every_created_observation_has_full_identity():

    store = make_store()
    observation = store.create(
        kind=ObservationKind.ACCESSIBILITY_TREE,
        source="device",
        data={"nodes": {"n1": {"text": "Search"}}},
        task_id="task_abc123456789abcd",
        run_id="run_abc123456789abcd",
        session_id="session_abc1234567",
    )

    assert is_valid_id(observation.observation_id)
    assert observation.content_hash.startswith("sha256:")
    assert observation.kind == ObservationKind.ACCESSIBILITY_TREE
    assert observation.run_id.startswith("run_")


def test_same_content_hashes_equal_different_content_does_not():

    first = hash_payload({"a": 1, "b": [1, 2]})
    same_keys_other_order = hash_payload({"b": [1, 2], "a": 1})
    changed = hash_payload({"a": 1, "b": [1, 2, 3]})

    assert first == same_keys_other_order
    assert first != changed


def test_two_captures_of_the_same_screen_get_distinct_hashes_when_content_changes():

    clock = TickingClock()
    store = ObservationStore(clock=clock)

    before = store.create(
        kind=ObservationKind.ACCESSIBILITY_TREE,
        source="device",
        data={"package": "com.youtube", "nodes": {"n1": {}}},
    )
    clock.advance(1)
    after = store.create(
        kind=ObservationKind.ACCESSIBILITY_TREE,
        source="device",
        data={"package": "com.youtube", "nodes": {"n1": {}, "n2": {}}},
    )

    assert before.content_hash != after.content_hash


def test_freshness_reports_age_honestly_and_never_lies():

    clock = TickingClock()
    store = ObservationStore(clock=clock)

    observation = store.create(
        kind=ObservationKind.FOREGROUND_APP,
        source="device",
        data={"package": "com.aura"},
    )

    assert observation.freshness(now=clock.now) == "fresh"

    clock.advance(DEFAULT_FRESH_WINDOW_S + 0.5)

    assert observation.freshness(now=clock.now) == "stale"
    assert observation.is_fresh(now=clock.now) is False


def test_require_fresh_refuses_stale_instead_of_returning_it():

    clock = TickingClock()
    store = ObservationStore(clock=clock)

    store.create(
        kind=ObservationKind.SCREENSHOT, source="device", data={"frame": 1}
    )
    clock.advance(DEFAULT_FRESH_WINDOW_S * 3)

    # The one behaviour the old system lacked: demanding current state
    # fails loudly instead of silently receiving minutes-old pixels.
    try:
        store.require_fresh(ObservationKind.SCREENSHOT, now=clock.now)
    except StaleObservationError:
        pass
    else:
        raise AssertionError("stale observation passed as fresh")


def test_latest_is_scoped_per_run_so_tasks_cannot_read_each_others_state():

    store = make_store()

    other_run_obs = store.create(
        kind=ObservationKind.FOREGROUND_APP,
        source="device",
        data={"package": "com.other"},
        run_id="run_other123456789a",
    )
    this_run_obs = store.create(
        kind=ObservationKind.FOREGROUND_APP,
        source="device",
        data={"package": "com.mine"},
        run_id="run_mine00000000001",
    )

    latest = store.latest(
        ObservationKind.FOREGROUND_APP, run_id="run_mine00000000001"
    )

    assert latest.observation_id == this_run_obs.observation_id
    assert latest.observation_id != other_run_obs.observation_id


def test_recording_a_device_observation_completes_missing_bookkeeping():

    store = make_store()

    recorded = store.record(
        Observation(
            observation_id="",           # device did not mint one
            kind=ObservationKind.DEVICE_STATE,
            source="android-step-endpoint",
            observed_at=time.time(),
            content_hash="",             # or could not compute one
            data={"battery": 88},
        )
    )

    assert is_valid_id(recorded.observation_id)
    assert recorded.content_hash.startswith("sha256:")


def test_recording_rejects_foreign_observation_ids():
    import pytest

    store = make_store()

    with pytest.raises(ValueError):
        store.record(
            Observation(
                observation_id="not-a-valid-id",
                kind=ObservationKind.DEVICE_STATE,
                source="somewhere",
                observed_at=time.time(),
                content_hash="sha256:x",
                data={},
            )
        )


def test_observations_are_immutable_snapshots():
    import dataclasses
    import pytest

    store = make_store()
    observation = store.create(
        kind=ObservationKind.FOREGROUND_APP, source="device", data={"p": "x"}
    )

    # Field identity cannot be rewritten: a changed screen means a new
    # observation, never an edit in place.
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.observed_at = 0.0