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
            "vision": None,
            "tools": None,
            "tts": None,
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
        assert "gsk_live" not in response.text
        assert "SECRETVALUE" not in response.text
        assert "401" not in response.text

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
