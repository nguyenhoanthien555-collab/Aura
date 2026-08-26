"""
Phase 10: the Android ↔ server settings contract.

`tests/test_settings_api.py` already pins Phase 9's security surface -
masking, encryption, authentication, unknown paths, keys never leaving
the server. This file covers only what Phase 10 added on top, and it is
deliberately the awkward half:

* `tools.auto_approve` refusing an empty list, because `[]` does not
  mean what it looks like (`ToolPolicy.from_config` reads
  `auto_approve or ["safe"]`, so an empty list silently re-approves safe
  tools). A validator that accepted it would store a value the running
  policy contradicts.
* The paths this phase made settable, at their bounds rather than in
  their middle - and the three that stayed un-settable on purpose.
* `configurable` reporting the whole allow-list, since the phone renders
  its locks from that field and a path missing from it is a control the
  user cannot reach.
* The three subsystem-conditional handlers *demoting* a path to
  `restart_required` when the subsystem is absent. `applied` is a
  promise the UI shows as "in effect now"; a handler that returned early
  and left the path in `applied` would be the lie this phase forbids.
* `/api/providers/health`'s per-provider map: every legal state, keyless
  providers, and one provider raising without taking the map - or the
  route - down with it.
"""

from types import SimpleNamespace

import pytest

from core.settings_store import ALLOWED, RuntimeSettings, SettingsError


# A key shaped like a real one, so a leak test has something to search
# for that could not appear by coincidence.
KEY = "gsk_a-really-long-fake-key-value-12345678ABCD"

AUTH = {"Authorization": "Bearer test-token"}


# ----------------------------------------------------------------------
# tools.auto_approve - the validator with a trap behind it
# ----------------------------------------------------------------------

class TestRiskLevels:

    def store(self, tmp_path) -> RuntimeSettings:
        return RuntimeSettings(path=tmp_path / "settings.json")

    def test_accepts_the_three_levels(self, tmp_path):
        accepted = self.store(tmp_path).update(
            {"tools": {"auto_approve": ["safe", "sensitive", "dangerous"]}}
        )
        assert accepted["tools.auto_approve"] == [
            "safe", "sensitive", "dangerous"
        ]

    def test_normalises_case_and_whitespace(self, tmp_path):
        accepted = self.store(tmp_path).update(
            {"tools": {"auto_approve": [" SAFE ", "Sensitive"]}}
        )
        assert accepted["tools.auto_approve"] == ["safe", "sensitive"]

    def test_drops_duplicates_keeping_order(self, tmp_path):
        accepted = self.store(tmp_path).update(
            {"tools": {"auto_approve": ["sensitive", "safe", "sensitive"]}}
        )
        assert accepted["tools.auto_approve"] == ["sensitive", "safe"]

    def test_rejects_a_level_this_codebase_does_not_define(self, tmp_path):
        # Not a typo guard: `ToolPolicy.from_config` logs and drops an
        # unknown level, so accepting one would persist a setting that
        # does nothing.
        with pytest.raises(SettingsError):
            self.store(tmp_path).update(
                {"tools": {"auto_approve": ["safe", "catastrophic"]}}
            )

    def test_rejects_an_empty_list(self, tmp_path):
        # The subtle one. `[]` reads as "confirm everything" and is not:
        # `from_config` falls back to `["safe"]`, so the running policy
        # would auto-approve safe tools while the stored setting claimed
        # nothing was auto-approved.
        with pytest.raises(SettingsError) as raised:
            self.store(tmp_path).update({"tools": {"auto_approve": []}})

        assert "at least one" in str(raised.value)

    def test_rejects_a_bare_string(self, tmp_path):
        # "safe" is a list of four characters, and iterating it would
        # produce four invalid levels rather than one valid one.
        with pytest.raises(SettingsError):
            self.store(tmp_path).update({"tools": {"auto_approve": "safe"}})

    def test_rejects_a_blank_entry(self, tmp_path):
        with pytest.raises(SettingsError):
            self.store(tmp_path).update(
                {"tools": {"auto_approve": ["safe", ""]}}
            )

    def test_vocabulary_is_tool_risk_not_a_local_list(self, tmp_path):
        # Every level `ToolRisk` defines must be accepted, so adding one
        # to the enum cannot leave this validator silently rejecting it.
        from tools.base import ToolRisk

        levels = [level.value for level in ToolRisk]

        accepted = self.store(tmp_path).update(
            {"tools": {"auto_approve": levels}}
        )

        assert set(accepted["tools.auto_approve"]) == set(levels)


# ----------------------------------------------------------------------
# The paths this phase made settable, at their edges
# ----------------------------------------------------------------------

class TestNewSettablePaths:

    def store(self, tmp_path) -> RuntimeSettings:
        return RuntimeSettings(path=tmp_path / "settings.json")

    @pytest.mark.parametrize(
        "changes, path, expected",
        [
            ({"server": {"screen": {"min_interval": 30}}},
             "server.screen.min_interval", 30.0),
            ({"tools": {"enabled": True}}, "tools.enabled", True),
            ({"tools": {"timeout": 45}}, "tools.timeout", 45.0),
            ({"voice": {"tts": {"provider": "edge"}}},
             "voice.tts.provider", "edge"),
            ({"voice": {"tts": {"voice": "en-US-AriaNeural"}}},
             "voice.tts.voice", "en-US-AriaNeural"),
            ({"voice": {"tts": {"volume": 80}}}, "voice.tts.volume", 80),
            ({"voice": {"tts": {"playback": False}}},
             "voice.tts.playback", False),
        ],
    )
    def test_accepted_and_coerced(self, tmp_path, changes, path, expected):
        accepted = self.store(tmp_path).update(changes)
        assert accepted[path] == expected
        assert type(accepted[path]) is type(expected)

    @pytest.mark.parametrize(
        "changes",
        [
            # Below the floor: an interval under a second would let the
            # phone stream observations faster than Aura can use them.
            {"server": {"screen": {"min_interval": 0.5}}},
            {"server": {"screen": {"min_interval": 301}}},
            {"tools": {"timeout": 0}},
            {"tools": {"timeout": 301}},
            {"voice": {"tts": {"volume": -1}}},
            {"voice": {"tts": {"volume": 101}}},
            # Free text, but not empty and not unbounded.
            {"voice": {"tts": {"provider": "   "}}},
            {"voice": {"tts": {"voice": "x" * 200}}},
        ],
    )
    def test_out_of_bounds_is_refused(self, tmp_path, changes):
        with pytest.raises(SettingsError):
            self.store(tmp_path).update(changes)

    def test_volume_stays_an_integer_percentage(self, tmp_path):
        # The provider converts it to edge-tts's "+80%" form; the setting
        # itself is a plain 0-100 so the phone can render a slider.
        accepted = self.store(tmp_path).update(
            {"voice": {"tts": {"volume": 0}}}
        )
        assert accepted["voice.tts.volume"] == 0


class TestDeliberatelyNotSettable:
    """
    A bearer token configures a capability; it does not grant one.

    These four are refused by the allow-list rather than validated, and
    the refusal is the feature. `allowed`, `allowed_paths` and
    `applications` decide which tools exist and which filesystem roots
    and executables they may reach. `rate` and `pitch` are owned by the
    mood pacing system, which would revert anything set here.
    """

    @pytest.mark.parametrize(
        "changes",
        [
            {"tools": {"allowed": ["file_read"]}},
            {"tools": {"allowed_paths": ["C:\\"]}},
            {"tools": {"applications": {"calc": "calc.exe"}}},
            {"voice": {"tts": {"rate": "+20%"}}},
            {"voice": {"tts": {"pitch": "+5Hz"}}},
        ],
    )
    def test_refused(self, tmp_path, changes):
        with pytest.raises(SettingsError):
            RuntimeSettings(path=tmp_path / "settings.json").update(changes)

    @pytest.mark.parametrize(
        "path",
        [
            "tools.allowed",
            "tools.allowed_paths",
            "tools.applications",
            "voice.tts.rate",
            "voice.tts.pitch",
        ],
    )
    def test_absent_from_the_allow_list(self, path):
        assert path not in ALLOWED


# ----------------------------------------------------------------------
# The zone Aura thinks it is in
# ----------------------------------------------------------------------

class TestTimezoneSetting:
    """
    `temporal.timezone` has existed in config.yaml since Phase 8 and is
    read by `TemporalClock.from_config`, but it was not in ALLOWED - so
    the phone was *told* the value in `effective` and could never change
    it. On a container that runs in UTC while its owner does not, that is
    Aura being confidently wrong about the time, which section 16 rules
    out more firmly than it rules out not knowing at all.
    """

    def store(self, tmp_path) -> RuntimeSettings:
        return RuntimeSettings(path=tmp_path / "settings.json")

    def test_in_the_allow_list(self):
        assert "temporal.timezone" in ALLOWED

    def test_a_resolvable_zone_is_accepted(self, tmp_path):
        accepted = self.store(tmp_path).update(
            {"temporal": {"timezone": "UTC"}}
        )
        assert accepted["temporal.timezone"] == "UTC"

    @pytest.mark.parametrize("written", ["utc", "  Utc ", "gmt", "Z"])
    def test_the_utc_aliases_normalise(self, tmp_path, written):
        # `resolve_timezone` already treats all three as UTC. Keeping the
        # owner's casing would put "utc" into the prompt's TIME section,
        # which reads as some zone other than the one they picked.
        accepted = self.store(tmp_path).update(
            {"temporal": {"timezone": written}}
        )
        assert accepted["temporal.timezone"] == "UTC"

    def test_an_unresolvable_zone_is_refused_and_names_the_fix(self, tmp_path):
        with pytest.raises(SettingsError) as error:
            self.store(tmp_path).update(
                {"temporal": {"timezone": "Mars/Olympus_Mons"}}
            )

        # Storing it would resolve to None at every read, leaving Aura on
        # the host's clock while the settings screen showed Mars: exactly
        # the dead setting `validate_path` refuses by name. And on Windows
        # the cause is usually one missing package, so the message says
        # so rather than leaving the owner to guess at their own typo.
        assert "tzdata" in str(error.value).lower()

    @pytest.mark.parametrize("written", ["", "   ", None])
    def test_nothing_clears_it_back_to_the_machine_zone(self, tmp_path, written):
        # Clearing has to be expressible or a zone set once is permanent -
        # the same reason `llm.custom_base_url` accepts "".
        accepted = self.store(tmp_path).update(
            {"temporal": {"timezone": written}}
        )
        assert accepted["temporal.timezone"] == ""

    def test_a_zone_is_not_lowercased(self, tmp_path):
        # IANA keys are case-sensitive to `ZoneInfo`, so the coercion that
        # `_provider_name` applies would break every real zone name. Only
        # the three UTC aliases are rewritten.
        try:
            accepted = self.store(tmp_path).update(
                {"temporal": {"timezone": "Asia/Ho_Chi_Minh"}}
            )
        except SettingsError:
            # No timezone database on this machine, which is the Windows
            # default. The claim under test cannot be observed here, and
            # inventing a pass would hide that.
            pytest.skip("no system timezone database")

        assert accepted["temporal.timezone"] == "Asia/Ho_Chi_Minh"


# ----------------------------------------------------------------------
# `applied` is a promise: the conditional handlers must keep it
# ----------------------------------------------------------------------

@pytest.fixture
def build_service(tmp_path):
    """
    A `SettingsService` over a throwaway overlay and a stand-in runtime.

    The overlay is installed process-wide rather than merely handed to
    the service: the three conditional handlers re-read their section
    through `load_config()`, so a service holding a store nobody else
    can see would apply the previous value and report success. The
    autouse conftest fixture clears the singleton again afterwards.

    Subsystems default to absent, which is the interesting case - it is
    what a headless Render deployment looks like, and what every honest
    demotion below depends on.
    """

    from core import settings_store
    from server.settings_service import SettingsService

    store = RuntimeSettings(path=tmp_path / "settings.json")
    settings_store.set_runtime_settings(store)

    def build(**services):

        present = {
            "proactive": None,
            "memory": None,
            "pipeline": None,
            "knowledge": None,
            "vision": None,
            "tools": None,
            "tts": None,
            "clock": None,
        }
        present.update(services)

        return SettingsService(
            SimpleNamespace(
                settings_store=store,
                services=SimpleNamespace(**present),
                # Never reached: no test here touches an `llm.*` path, so
                # the provider chain is not rebuilt.
                engine=SimpleNamespace(conversation=SimpleNamespace(llm=None)),
            )
        )

    return build


class TestLiveApplyHonesty:

    def test_screen_interval_moves_a_live_manager(self, build_service):
        manager = SimpleNamespace(min_interval=8.0)

        report = build_service(vision=manager).apply(
            {"server": {"screen": {"min_interval": 30}}}
        )

        assert manager.min_interval == 30.0
        assert report["applied"] == ["server.screen.min_interval"]
        assert report["restart_required"] == []
        assert report["needs_restart"] is False

    def test_screen_interval_demoted_without_a_manager(self, build_service):
        service = build_service()

        report = service.apply({"server": {"screen": {"min_interval": 30}}})

        # Persisted either way - the change is real, it just is not live.
        assert service.runtime.settings_store.overrides[
            "server.screen.min_interval"
        ] == 30.0
        assert report["applied"] == []
        assert report["restart_required"] == ["server.screen.min_interval"]
        assert report["needs_restart"] is True

    def test_recall_moves_a_live_pipeline(self, build_service):
        """
        `Services` keeps `pipeline` as a sibling of `memory`, not inside
        it - line 36 next to line 31 - so a handler that reaches through
        the memory manager finds nothing and reports success anyway.
        The stand-in below is deliberately shaped like the real
        `MemoryManager`: it has no `pipeline` attribute at all.
        """

        pipeline = SimpleNamespace(recall_enabled=True)

        report = build_service(
            memory=SimpleNamespace(session=None), pipeline=pipeline
        ).apply({"memory": {"recall": False}})

        assert pipeline.recall_enabled is False
        assert report["applied"] == ["memory.recall"]
        assert report["restart_required"] == []

    def test_recall_back_on_moves_it_back(self, build_service):

        pipeline = SimpleNamespace(recall_enabled=False)

        build_service(pipeline=pipeline).apply({"memory": {"recall": True}})

        assert pipeline.recall_enabled is True

    def test_recall_on_swaps_the_legacy_retriever_live(self, build_service):
        """
        `memory.recall` gates two mechanisms and `applied` is a promise
        about both. `MemoryKnowledgeProvider.retriever` is a plain
        attribute read on every turn through `_recalled`, so the older
        half is swappable and does not need a restart to honour the
        toggle - which is what stops `applied` from being half true.
        """

        from memory.retrieval import KeywordRetriever, NullRetriever

        knowledge = SimpleNamespace(retriever=NullRetriever())

        report = build_service(
            memory=SimpleNamespace(session=object()), knowledge=knowledge
        ).apply({"memory": {"recall": True}})

        assert isinstance(knowledge.retriever, KeywordRetriever)
        assert report["applied"] == ["memory.recall"]

    def test_recall_off_swaps_the_legacy_retriever_back(self, build_service):

        from memory.retrieval import KeywordRetriever, NullRetriever

        knowledge = SimpleNamespace(
            retriever=KeywordRetriever(session=object())
        )

        build_service(
            memory=SimpleNamespace(session=object()), knowledge=knowledge
        ).apply({"memory": {"recall": False}})

        assert isinstance(knowledge.retriever, NullRetriever)

    def test_recall_moves_both_halves_at_once(self, build_service):

        from memory.retrieval import KeywordRetriever, NullRetriever

        pipeline = SimpleNamespace(recall_enabled=False)
        knowledge = SimpleNamespace(retriever=NullRetriever())

        report = build_service(
            memory=SimpleNamespace(session=object()),
            pipeline=pipeline,
            knowledge=knowledge,
        ).apply({"memory": {"recall": True}})

        assert pipeline.recall_enabled is True
        assert isinstance(knowledge.retriever, KeywordRetriever)
        assert report["applied"] == ["memory.recall"]

    def test_the_legacy_half_is_skipped_without_a_session(self, build_service):
        """
        `KeywordRetriever.__init__` opens its own database when handed no
        session. A settings PATCH is not the place to do that, so the
        older half is left alone and the pipeline half still counts.
        """

        from memory.retrieval import NullRetriever

        knowledge = SimpleNamespace(retriever=NullRetriever())
        pipeline = SimpleNamespace(recall_enabled=False)

        report = build_service(
            memory=None, pipeline=pipeline, knowledge=knowledge
        ).apply({"memory": {"recall": True}})

        assert isinstance(knowledge.retriever, NullRetriever)
        assert pipeline.recall_enabled is True
        assert report["applied"] == ["memory.recall"]

    def test_recall_demoted_without_a_pipeline(self, build_service):
        """
        A deployment with `memory.pipeline` off has no pipeline to move.
        The setting still persists - it decides what the next start
        does - but it must be reported as needing one, not as applied.
        """

        service = build_service()

        report = service.apply({"memory": {"recall": False}})

        assert service.runtime.settings_store.overrides["memory.recall"] is False
        assert report["applied"] == []
        assert report["restart_required"] == ["memory.recall"]

    def test_tools_replace_the_running_policy(self, build_service):
        from tools.base import ToolRisk
        from tools.executor import ToolPolicy

        executor = SimpleNamespace(policy=ToolPolicy())

        report = build_service(tools=executor).apply({
            "tools": {
                "enabled": True,
                "auto_approve": ["safe", "sensitive"],
                "timeout": 45,
            }
        })

        assert executor.policy.enabled is True
        assert executor.policy.auto_approve == frozenset(
            {ToolRisk.SAFE, ToolRisk.SENSITIVE}
        )
        assert executor.policy.timeout == 45.0

        assert sorted(report["applied"]) == [
            "tools.auto_approve", "tools.enabled", "tools.timeout"
        ]
        assert report["needs_restart"] is False

    def test_tools_demoted_without_an_executor(self, build_service):
        report = build_service().apply({"tools": {"timeout": 45}})

        assert report["applied"] == []
        assert report["restart_required"] == ["tools.timeout"]

    def test_voice_moves_a_live_provider(self, build_service):
        provider = SimpleNamespace(voice="en-GB-SoniaNeural", volume="+0%")

        report = build_service(
            tts=SimpleNamespace(provider=provider)
        ).apply({"voice": {"tts": {"voice": "en-US-AriaNeural", "volume": 80}}})

        assert provider.voice == "en-US-AriaNeural"
        # Through `normalise_percent`, not assigned bare: edge-tts wants
        # the signed-percentage form, and 80 would mean nothing to it.
        assert provider.volume == "+80%"

        assert sorted(report["applied"]) == [
            "voice.tts.voice", "voice.tts.volume"
        ]

    def test_voice_demoted_without_an_engine(self, build_service):
        report = build_service().apply(
            {"voice": {"tts": {"volume": 80}}}
        )

        assert report["applied"] == []
        assert report["restart_required"] == ["voice.tts.volume"]

    def test_engine_and_playback_always_need_a_restart(self, build_service):
        # Even with a live provider: one selects the class, the other is
        # decided when the audio player is built.
        provider = SimpleNamespace(voice="en-GB-SoniaNeural", volume="+0%")

        report = build_service(
            tts=SimpleNamespace(provider=provider)
        ).apply({"voice": {"tts": {"provider": "edge", "playback": False}}})

        assert report["applied"] == []
        assert sorted(report["restart_required"]) == [
            "voice.tts.playback", "voice.tts.provider"
        ]
        assert report["needs_restart"] is True

    def test_one_absent_subsystem_does_not_demote_another(self, build_service):
        # A PATCH spanning two subsystems, one live and one not. The
        # report has to split, not round in either direction.
        manager = SimpleNamespace(min_interval=8.0)

        report = build_service(vision=manager).apply({
            "server": {"screen": {"min_interval": 30}},
            "tools": {"timeout": 45},
        })

        assert report["applied"] == ["server.screen.min_interval"]
        assert report["restart_required"] == ["tools.timeout"]
        assert report["needs_restart"] is True

    def test_a_timezone_moves_the_live_clock(self, build_service):
        # Not a rebuild. `launcher/services.py` hands one clock object to
        # six subsystems, so the only change that reaches all of them is
        # one made in place on the object they are already holding.
        from core.temporal import TemporalClock

        clock = TemporalClock()

        report = build_service(clock=clock).apply(
            {"temporal": {"timezone": "UTC"}}
        )

        assert clock.timezone_name == "UTC"
        assert clock.context().utc_offset == "+00:00"
        assert report["applied"] == ["temporal.timezone"]
        assert report["restart_required"] == []

    def test_a_timezone_is_demoted_without_a_clock(self, build_service):
        service = build_service()

        report = service.apply({"temporal": {"timezone": "UTC"}})

        # Persisted either way: the next process reads it from the overlay
        # through `TemporalClock.from_config`.
        assert service.runtime.settings_store.overrides[
            "temporal.timezone"
        ] == "UTC"
        assert report["applied"] == []
        assert report["restart_required"] == ["temporal.timezone"]
        assert report["needs_restart"] is True

    def test_clearing_the_timezone_reaches_the_live_clock_too(
        self, build_service
    ):
        from core.temporal import TemporalClock

        clock = TemporalClock(timezone_name="UTC")

        report = build_service(clock=clock).apply(
            {"temporal": {"timezone": ""}}
        )

        assert clock.timezone is None
        assert clock.timezone_name == ""
        assert report["applied"] == ["temporal.timezone"]


# ----------------------------------------------------------------------
# The API surface the phone parses
# ----------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    """
    A live app with isolated stores, plus an auth header.

    Same construction as `tests/test_settings_api.py`'s fixture, and for
    the same reason: `server.config.settings` was built from the
    environment at import time, so the token has to be set on the
    singleton rather than through `AURA_SERVER_AUTH_TOKEN`.
    """

    from fastapi.testclient import TestClient
    from server import config as server_config
    from server.main import app
    from server.runtime import init_runtime, shutdown_runtime

    from core import credentials, settings_store

    monkeypatch.setenv("AURA_SECRET_KEY", "a-test-secret-for-the-key-store")

    previous_token = server_config.settings.auth_token
    server_config.settings.auth_token = "test-token"

    credentials._store = None
    settings_store._settings = None

    init_runtime()

    client = TestClient(app)

    yield client

    client.close()
    shutdown_runtime()
    credentials._store = None
    settings_store._settings = None
    server_config.settings.auth_token = previous_token


class TestConfigurableContract:
    """
    The phone locks its controls from `configurable`.

    A path missing from that list renders as unavailable even though the
    server would accept it, so the list has to be the allow-list itself
    rather than a hand-maintained copy of it.
    """

    def test_reports_every_allowed_path(self, api):
        body = api.get("/api/settings", headers=AUTH).json()
        assert body["configurable"] == sorted(ALLOWED)

    @pytest.mark.parametrize(
        "path",
        [
            "server.screen.min_interval",
            "tools.enabled",
            "tools.auto_approve",
            "tools.timeout",
            "voice.tts.provider",
            "voice.tts.voice",
            "voice.tts.volume",
            "voice.tts.playback",
            "temporal.timezone",
        ],
    )
    def test_includes_the_paths_this_phase_added(self, api, path):
        # By name as well as by set equality: removing one from ALLOWED
        # would keep the test above passing while silently greying out a
        # control on the phone.
        body = api.get("/api/settings", headers=AUTH).json()
        assert path in body["configurable"]

    def test_defaults_are_intact_with_no_overrides(self, api):
        body = api.get("/api/settings", headers=AUTH).json()

        assert body["overrides"] == {}

        effective = body["effective"]

        # The one default this phase promised not to move. Phase 8 shipped
        # proactive off, and a settings screen is exactly where it could
        # get quietly turned on.
        assert effective["proactive"]["enabled"] is False

    def test_the_shipped_config_is_writable_back(self, api):
        # An invariant that is easy to break from the other end: if
        # `config.yaml` held a value the validator refuses, the phone
        # would render a state it could never save. `auto_approve` is the
        # one at risk, since an empty list is refused.
        effective = api.get("/api/settings", headers=AUTH).json()["effective"]

        approved = effective["tools"]["auto_approve"]

        assert approved
        assert set(approved) <= {"safe", "sensitive", "dangerous"}


class TestProviderHealthRoute:

    def test_shape_the_phone_parses(self, api):
        body = api.get("/api/providers/health", headers=AUTH).json()

        for field in ("requested", "active", "chain", "in_fallback",
                      "problems", "ready", "providers"):
            assert field in body

        assert isinstance(body["providers"], dict)

        for name, entry in body["providers"].items():
            assert set(entry) >= {"configured", "healthy", "state", "in_chain"}
            assert isinstance(entry["configured"], bool)
            assert isinstance(entry["healthy"], bool)
            assert isinstance(entry["in_chain"], bool)
            assert entry["state"] in {
                "active", "standby", "failed", "idle", "unconfigured", "error"
            }

    def test_every_known_provider_is_reported(self, api):
        from server.routes.settings import PROVIDER_CAPABILITIES

        body = api.get("/api/providers/health", headers=AUTH).json()

        # Not a subset: the phone renders one row per provider and a
        # missing key would read as "this provider does not exist".
        assert set(body["providers"]) == set(PROVIDER_CAPABILITIES)

    def test_no_key_material_anywhere_in_the_response(self, api):
        from core.credentials import get_credential_store

        get_credential_store().set("groq", KEY)

        response = api.get("/api/providers/health", headers=AUTH)

        assert response.status_code == 200
        assert KEY not in response.text
        # Not even the tail a masked form would show.
        assert "ABCD" not in response.text

    def test_survives_an_unreadable_provider(self, api, monkeypatch):
        from core.credentials import get_credential_store

        store = get_credential_store()

        def unreadable(name):
            if name == "groq":
                raise RuntimeError("key store is on fire")
            return False

        monkeypatch.setattr(store, "has", unreadable)

        response = api.get("/api/providers/health", headers=AUTH)

        # The route must not 500, and the one bad entry must not blank
        # the map: a settings screen that shows nothing is worse than one
        # that shows five providers and an error.
        assert response.status_code == 200

        providers = response.json()["providers"]

        assert providers["groq"]["state"] == "error"
        assert providers["groq"]["healthy"] is False
        assert providers["groq"]["configured"] is False

        # A category, never the message - this is rendered on a phone.
        assert providers["groq"]["problem"] == "RuntimeError"
        assert "on fire" not in response.text

        assert providers["ollama"]["configured"] is True
        assert len(providers) > 1


class TestHealthSurvivesADeadProvider:
    """
    `/api/health` is the rung everything else hangs off.

    The phone treats a 200 here as "the server is reachable and took my
    token", because the route is behind `verify_token`. So this endpoint
    failing does not read as "one subsystem is unwell" - it reads as
    "there is no Aura at this address", and the app sends the user to the
    connection screen.

    It used to fail for the worst possible reason. `health_status()`
    called `active_chain()` bare, and that property builds the provider on
    first access, and construction raises when the key is missing or
    invalid. A user whose provider key had died - the exact person the
    Control Hub exists for - was told their connection was broken, and
    the screen that could have fixed it was locked behind that verdict.

    Found by running the server with no key and calling the route, which
    is why these tests exist rather than a comment saying it was
    considered.
    """

    def test_health_is_200_when_the_provider_cannot_be_built(self, api, monkeypatch):

        from server.runtime import get_runtime

        runtime = get_runtime()

        # Exactly what BrainRouter does with a missing key: raise from the
        # lazy property, on the first call, from inside health_status.
        def dead_chain():
            raise ValueError("Primary provider gemini could not be initialized")

        monkeypatch.setattr(
            runtime.engine.conversation.llm, "active_chain", dead_chain,
            raising=False,
        )

        response = api.get("/api/health", headers=AUTH)

        assert response.status_code == 200

    def test_it_names_the_failure_as_a_subsystem_state(self, api, monkeypatch):

        from server.runtime import get_runtime

        runtime = get_runtime()

        def dead_chain():
            raise ValueError("Primary provider gemini could not be initialized")

        monkeypatch.setattr(
            runtime.engine.conversation.llm, "active_chain", dead_chain,
            raising=False,
        )

        body = api.get("/api/health", headers=AUTH).json()

        # Still a string in the same slot, so a client that renders the
        # runtime map keeps working without a new field.
        assert body["runtime"]["llm_provider"] == "unavailable (ValueError)"

        # The other seven subsystems are still reported. One dead provider
        # must not blank the diagnostics screen.
        assert body["runtime"]["memory"] in {"connected", "unavailable"}
        assert len(body["runtime"]) >= 8

    def test_the_provider_error_message_is_not_in_the_response(self, api, monkeypatch):

        from server.runtime import get_runtime

        runtime = get_runtime()

        # A provider's exception can quote the credential it was rejected
        # for, so the type name is all that may cross the wire.
        def leaky_chain():
            raise ValueError("401 from provider: key gsk_liveSECRETVALUE rejected")

        monkeypatch.setattr(
            runtime.engine.conversation.llm, "active_chain", leaky_chain,
            raising=False,
        )

        response = api.get("/api/health", headers=AUTH)

        assert response.status_code == 200

        # Credential fragments are distinctive enough to look for in the
        # whole body.
        assert "gsk_live" not in response.text
        assert "SECRETVALUE" not in response.text

        # The HTTP status the provider quoted is checked against the field
        # that would carry it, not the whole body. `"401" not in
        # response.text` was the same assertion for a while and was
        # flaky at about 1% per run: `runtime.uptime_seconds` is an
        # unrounded `time.time()` delta, so roughly one health response in
        # eighty happens to contain the digits 401 somewhere in its
        # decimals - e.g. 7.330945879406765 - and the test failed for a
        # reason that had nothing to do with a leak. A security assertion
        # that cries wolf teaches people to re-run it.
        assert response.json()["runtime"]["llm_provider"] == "unavailable (ValueError)"

    def test_a_healthy_provider_still_reports_its_chain(self, api):

        # The guard must not have flattened the useful case: the whole
        # reason `active_chain()` is called instead of `provider_name` is
        # that it reports what was actually built.
        body = api.get("/api/health", headers=AUTH).json()

        assert body["runtime"]["llm_provider"]
        assert body["status"] in {"healthy", "starting"}

    def test_a_conversation_without_a_model_does_not_500_either(self, api, monkeypatch):

        from server.runtime import get_runtime

        runtime = get_runtime()

        # `readiness_status` already guarded this; health did not.
        class NoModel:
            @property
            def llm(self):
                raise AttributeError("conversation has no llm")

        monkeypatch.setattr(runtime.engine, "conversation", NoModel())

        response = api.get("/api/health", headers=AUTH)

        assert response.status_code == 200
        assert response.json()["runtime"]["llm_provider"] == "unavailable"

    def test_health_still_needs_the_token(self, api):

        # The rung only means "authenticated" if it is actually gated, and
        # the whole ladder is built on that being true.
        assert api.get("/api/health").status_code == 401


class TestReportsTrackWrites:
    """
    What a GET says after a PATCH.

    `ServerRuntime.config` is materialised once in the constructor, and
    deliberately so: `build_services` hands that dict to every subsystem,
    and a config mutating under a running pipeline is the failure mode
    `restart_required` exists to avoid.

    But it is also the source for every *report* - `effective` in
    `GET /api/settings`, `primary` and `is_primary` in `GET /api/providers`
    - so a settings write left the reports describing process start. The
    write persisted, the live subsystem took the new value (every
    `_reapply_*` handler reads `load_config()` fresh), and then the next
    GET answered with the old one. The phone renders its controls from
    `effective`, so a switch the user had just moved sprang back while the
    server used the new value.

    Nothing in the old test suite caught it, because these tests assert on
    `overrides` and on the PATCH report - both of which were right. Only a
    read-after-write against a real runtime shows it, which is what these
    do.
    """

    def patch(self, api, body):

        response = api.patch("/api/settings", json={"settings": body}, headers=AUTH)

        assert response.status_code == 200, response.text

        return response.json()

    def test_effective_reflects_a_write_on_the_next_get(self, api):

        before = api.get("/api/settings", headers=AUTH).json()

        assert before["effective"]["tools"]["timeout"] != 60.0

        self.patch(api, {"tools": {"timeout": 60}})

        after = api.get("/api/settings", headers=AUTH).json()

        assert after["overrides"]["tools.timeout"] == 60.0
        assert after["effective"]["tools"]["timeout"] == 60.0

    def test_the_patch_reply_reports_the_value_it_just_wrote(self, api):

        # The client uses this to re-render without a second round trip, so
        # a stale `effective` here is a control that visibly springs back.
        report = self.patch(api, {"tools": {"timeout": 55}})

        assert report["applied"] == ["tools.timeout"]
        assert report["effective"]["tools"]["timeout"] == 55.0

    def test_a_nested_write_reflects_too(self, api):

        self.patch(api, {"server": {"screen": {"min_interval": 30}}})

        body = api.get("/api/settings", headers=AUTH).json()

        assert body["effective"]["server"]["screen"]["min_interval"] == 30.0

    def test_the_provider_list_tracks_the_configured_primary(self, api):

        self.patch(api, {"llm": {"provider": "groq"}})

        body = api.get("/api/providers", headers=AUTH).json()

        # `primary` and the per-entry flag come from the same snapshot, so
        # both were stale together - the AI & Models screen showed the old
        # provider as selected right after the user picked a new one.
        assert body["primary"] == "groq"

        primaries = [p["name"] for p in body["providers"] if p["is_primary"]]

        assert primaries == ["groq"]

    def test_reset_puts_the_reports_back(self, api):

        original = api.get("/api/settings", headers=AUTH).json()
        was = original["effective"]["tools"]["timeout"]

        self.patch(api, {"tools": {"timeout": 60}})

        # Assert the override is visible first, so this test cannot pass by
        # `effective` never having moved in the first place.
        mid = api.get("/api/settings", headers=AUTH).json()
        assert mid["effective"]["tools"]["timeout"] == 60.0

        response = api.post("/api/settings/reset", json={}, headers=AUTH)

        assert response.status_code == 200
        assert "tools.timeout" in response.json()["reset"]

        after = api.get("/api/settings", headers=AUTH).json()

        # Back to config.yaml, not to the code defaults, and reported as
        # such - otherwise a reset looks like it did nothing.
        assert after["overrides"] == {}
        assert after["effective"]["tools"]["timeout"] == was

    def test_a_rejected_write_leaves_the_reports_alone(self, api):

        before = api.get("/api/settings", headers=AUTH).json()

        response = api.patch(
            "/api/settings",
            json={"settings": {"tools": {"timeout": 9999}}},
            headers=AUTH,
        )

        assert response.status_code == 422

        after = api.get("/api/settings", headers=AUTH).json()

        assert after["effective"]["tools"] == before["effective"]["tools"]
        assert after["overrides"] == before["overrides"]

    def test_the_config_keeps_the_server_section_after_a_refresh(self, api):

        # `ServerRuntime.__init__` guarantees this key and code downstream
        # indexes it without checking. Replacing the snapshot has to keep
        # that guarantee, or an unrelated route starts raising KeyError.
        self.patch(api, {"tools": {"timeout": 42}})

        from server.runtime import get_runtime

        assert "server" in get_runtime().config

        assert api.get("/api/health", headers=AUTH).status_code == 200

    def test_the_owner_can_set_the_timezone_over_the_wire(self, api):

        # The whole point of putting `temporal.timezone` in the allow-list:
        # the phone is the only settings screen a container deployment has,
        # and `effective` has always shown this value. Reading a setting the
        # owner cannot write is the dead control section 2 rules out, so the
        # end-to-end path is worth pinning and not just the store.
        report = self.patch(api, {"temporal": {"timezone": "UTC"}})

        assert report["applied"] == ["temporal.timezone"]
        assert report["restart_required"] == []

        after = api.get("/api/settings", headers=AUTH).json()

        assert after["effective"]["temporal"]["timezone"] == "UTC"

    def test_an_unresolvable_timezone_is_refused_over_the_wire(self, api):

        before = api.get("/api/settings", headers=AUTH).json()

        response = api.patch(
            "/api/settings",
            json={"settings": {"temporal": {"timezone": "Mars/Olympus_Mons"}}},
            headers=AUTH,
        )

        assert response.status_code == 422

        # The refusal reaches the owner as words he can act on, not as a
        # bare 422 - `patch_settings` returns `SettingsError` verbatim
        # precisely so a validator can say what the fix is.
        assert "tzdata" in response.text.lower()

        after = api.get("/api/settings", headers=AUTH).json()

        # And nothing moved. Section 41: a bad value must not be able to
        # leave the owner's clock somewhere he did not put it.
        assert after["effective"]["temporal"] == before["effective"]["temporal"]


@pytest.fixture
def key_store(monkeypatch):
    """
    A credential store that can actually hold a key.

    The conftest fixture removes `AURA_SECRET_KEY` so a test run cannot
    leave behind ciphertext the next one could read. Without a secret,
    `set` keeps the key in memory and then raises to say it is not on
    disk - correct behaviour, covered elsewhere, and not what these tests
    are about. So they put a throwaway secret back and rebuild the store
    against the temporary path the conftest already redirected it to.
    """

    from core import credentials

    monkeypatch.setenv(credentials.SECRET_ENV_VAR, "a-test-secret-for-keys")

    credentials._store = None

    return credentials.get_credential_store()


class TestPerProviderStates:
    """
    The five honest states, tested directly.

    `_per_provider_health` is called with the chain the running
    `FallbackProvider` reports, and what that chain looks like in a test
    app is an accident of configuration. Calling the function with an
    explicit chain pins the semantics instead - which is what the phone
    renders a subtitle from.
    """

    def health(self, members, active):
        from server.routes.settings import _per_provider_health

        return _per_provider_health(members, active)
    def test_the_serving_provider_is_active_and_healthy(self):
        entry = self.health(["gemini", "groq"], "gemini")["gemini"]

        assert entry["state"] == "active"
        assert entry["healthy"] is True
        assert entry["in_chain"] is True

    def test_behind_the_active_one_is_standby(self):
        # Never asked, so nothing is known about it beyond its key.
        entry = self.health(["gemini", "groq"], "gemini")["groq"]

        assert entry["state"] == "standby"
        assert entry["healthy"] is False
        assert entry["in_chain"] is True

    def test_the_chain_moved_past_it_so_it_failed(self):
        # The only case where "failed" can be said honestly: the
        # fallback provider tried it and moved on.
        result = self.health(["gemini", "groq"], "groq")

        assert result["gemini"]["state"] == "failed"
        assert result["gemini"]["healthy"] is False
        assert result["groq"]["state"] == "active"

    def test_not_in_the_chain_with_a_key_is_idle(self, key_store):
        key_store.set("mistral", KEY)

        entry = self.health(["gemini"], "gemini")["mistral"]

        assert entry["state"] == "idle"
        assert entry["configured"] is True
        assert entry["in_chain"] is False
        # Idle is not a fault: it is simply not being used.
        assert entry["healthy"] is False

    def test_not_in_the_chain_without_a_key_is_unconfigured(self):
        entry = self.health(["gemini"], "gemini")["mistral"]

        assert entry["state"] == "unconfigured"
        assert entry["configured"] is False

    def test_keyless_providers_report_configured(self):
        # Ollama and the mock provider need no key, so "no key stored"
        # must not render as "not set up".
        result = self.health([], "")

        assert result["ollama"]["configured"] is True
        assert result["mock"]["configured"] is True

    def test_an_empty_chain_blames_nothing(self):
        # A server whose provider chain could not be read at all. Every
        # provider is out of the chain, and none of them failed.
        result = self.health([], "")

        assert {entry["state"] for entry in result.values()} <= {
            "idle", "unconfigured"
        }
        assert all(entry["healthy"] is False for entry in result.values())

    def test_nothing_in_an_entry_resembles_a_key(self, key_store):
        key_store.set("groq", KEY)

        entry = self.health(["groq"], "groq")["groq"]

        assert KEY not in repr(entry)
        assert set(entry) == {"configured", "healthy", "state", "in_chain"}


# ----------------------------------------------------------------------
# The provider registry the phone renders (Phase 11)
# ----------------------------------------------------------------------

class TestProviderRegistryContract:
    """
    `GET /api/providers` is the API-keys screen.

    Phase 11 added six providers, and the screen is generated from this
    response - so a provider Aura can build but does not describe is a
    provider the user cannot configure, and a provider described but not
    buildable is a row whose Test button always fails. Both directions are
    asserted here rather than trusted to review.
    """

    def test_every_buildable_provider_is_described(self):
        from brain.router import KEYLESS_PROVIDERS, PROVIDER_KEYS
        from server.routes.settings import PROVIDER_CAPABILITIES

        for name in (*PROVIDER_KEYS, *KEYLESS_PROVIDERS):
            assert name in PROVIDER_CAPABILITIES, f"{name} has no capabilities"

    def test_every_described_provider_can_be_built(self):
        from brain.router import KEYLESS_PROVIDERS, PROVIDER_KEYS
        from server.routes.settings import PROVIDER_CAPABILITIES

        known = {"mock", *PROVIDER_KEYS, *KEYLESS_PROVIDERS}

        for name in PROVIDER_CAPABILITIES:
            assert name in known, f"{name} is described but not registered"

    def test_the_model_setting_matches_what_the_router_reads(self):
        # The picker PATCHes `model_setting`. If it named a different path
        # than the router reads, the model would appear to change and the
        # request would carry the old one.
        from brain.router import HTTP_CHAT_PROVIDERS
        from server.routes.settings import PROVIDER_CAPABILITIES

        for name, (_, _, model_key) in HTTP_CHAT_PROVIDERS.items():
            assert (
                PROVIDER_CAPABILITIES[name]["model_setting"] == f"llm.{model_key}"
            )

    def test_every_model_setting_is_writable(self):
        # A path outside the allow-list is a control the phone renders and
        # the server then refuses.
        from server.routes.settings import PROVIDER_CAPABILITIES

        for name, caps in PROVIDER_CAPABILITIES.items():
            path = caps["model_setting"]

            if path:
                assert path in ALLOWED, f"{name} points the picker at {path}"

    def test_the_key_variable_matches_the_router(self):
        from brain.router import PROVIDER_KEYS
        from server.routes.settings import PROVIDER_CAPABILITIES

        for name, variable in PROVIDER_KEYS.items():
            assert PROVIDER_CAPABILITIES[name]["api_key_env"] == variable

    def test_the_response_carries_what_the_keys_screen_needs(self, api):
        body = api.get("/api/providers", headers=AUTH).json()

        by_name = {entry["name"]: entry for entry in body["providers"]}

        # The spec's per-provider fields: display name, id, base URL,
        # masked key, key source, configured state, capabilities, models,
        # primary/fallback standing.
        for name, entry in by_name.items():
            assert set(entry) >= {
                "name", "label", "api_base", "api_key_env", "model",
                "model_setting", "chat", "streaming", "tools", "vision",
                "models", "configured", "key_masked", "key_source",
                "is_primary", "is_fallback", "api_base_overridden",
            }, name

        # And all six new providers are actually there, not just claimed
        # in a document.
        for name in ("openai", "anthropic", "cerebras", "xai", "deepseek", "qwen"):
            assert name in by_name

    def test_a_custom_endpoint_is_reported_without_its_value(self, api, monkeypatch):
        # A gateway URL can carry a token in its query string, so the fact
        # of an override is published and the value never is.
        monkeypatch.setenv(
            "OPENAI_BASE_URL", "https://gateway.internal/v1?token=do-not-publish"
        )

        response = api.get("/api/providers", headers=AUTH)

        assert response.status_code == 200
        assert "do-not-publish" not in response.text

        entry = next(
            item for item in response.json()["providers"]
            if item["name"] == "openai"
        )

        assert entry["api_base_overridden"] is True
        assert entry["api_base"] == "https://api.openai.com/v1"

    def test_no_override_reports_false(self, api, monkeypatch):
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        entry = next(
            item
            for item in api.get("/api/providers", headers=AUTH).json()["providers"]
            if item["name"] == "openai"
        )

        assert entry["api_base_overridden"] is False

    def test_the_model_shown_is_the_one_that_would_be_sent(self, api):
        # Written through the API and read back from the provider list, so
        # the two agree by construction rather than by coincidence.
        api.patch(
            "/api/settings", headers=AUTH,
            json={"settings": {"llm": {"anthropic_model": "claude-opus-5"}}},
        )

        entry = next(
            item
            for item in api.get("/api/providers", headers=AUTH).json()["providers"]
            if item["name"] == "anthropic"
        )

        assert entry["model"] == "claude-opus-5"


class TestProviderTestRoute:
    """
    `POST /api/providers/test` has to actually build the provider.

    It did not. `_instantiate_provider` was an instance method and this
    route called it unbound, so `self` took the provider name and Python
    raised TypeError before any provider was constructed - which the route
    caught and reported as "not configured". Every press of the Test
    button answered "not configured", for every provider, whether or not
    its key was present. Nothing failed loudly, because "not configured"
    is a plausible answer on a deployment with no keys.
    """

    def test_a_provider_that_needs_no_key_reports_ok(self, api):
        # The mock provider is the only one that can be probed here: no
        # key, no network, and a real `generate`.
        body = api.post(
            "/api/providers/test", headers=AUTH, json={"provider": "mock"},
        ).json()

        assert body["provider"] == "mock"
        assert body["ok"] is True, body
        assert "latency_ms" in body

    def test_a_missing_key_names_the_variable_not_a_type_error(self, api, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        body = api.post(
            "/api/providers/test", headers=AUTH, json={"provider": "openai"},
        ).json()

        assert body["ok"] is False
        # The actionable answer, which is what the TypeError was hiding.
        assert body["error"] == "OPENAI_API_KEY is not set"

    def test_an_unimplemented_provider_is_refused(self, api):
        body = api.post(
            "/api/providers/test", headers=AUTH,
            json={"provider": "notaprovider"},
        ).json()

        assert body["ok"] is False
        assert body["error"] == "unknown provider"

    def test_a_refused_key_is_reported_as_a_key_problem(self, api, monkeypatch):
        # Distinguished from "unreachable": the fix is completely
        # different, and this string is what the phone shows.
        from brain.providers.errors import ProviderAuthError
        from brain.providers.openai import OpenAIProvider

        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
        monkeypatch.setattr(
            OpenAIProvider, "generate",
            lambda self, prompt: (_ for _ in ()).throw(
                ProviderAuthError("OpenAI rejected the API key")
            ),
        )

        response = api.post(
            "/api/providers/test", headers=AUTH, json={"provider": "openai"},
        )

        assert response.json()["ok"] is False
        assert response.json()["error"] == "invalid api key"
        assert "sk-not-a-real-key" not in response.text

    def test_an_exhausted_account_is_not_reported_as_unreachable(self, api, monkeypatch):
        from brain.providers.errors import ProviderRateLimitError
        from brain.providers.openai import OpenAIProvider

        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
        monkeypatch.setattr(
            OpenAIProvider, "generate",
            lambda self, prompt: (_ for _ in ()).throw(
                ProviderRateLimitError("429", is_account_limit=True)
            ),
        )

        body = api.post(
            "/api/providers/test", headers=AUTH, json={"provider": "openai"},
        ).json()

        assert body["error"] == "quota exhausted"

    def test_the_probe_needs_the_token(self, api):
        assert api.post(
            "/api/providers/test", json={"provider": "mock"},
        ).status_code in (401, 403)
