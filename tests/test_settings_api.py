"""
Phase 9 settings: the credential store, the overlay, and the API.

The security rules these tests pin are the spec's own: keys never leave
the server except masked, keys are never in logs or error text, settings
changes are authenticated, unknown settings are rejected, a masked value
is not accepted back as a real key, and a deleted key stops working in
the same process.
"""

import json
import os

import pytest

from core.credentials import CredentialError, CredentialStore, mask
from core.settings_store import RuntimeSettings, SettingsError


SECRET = "test-secret-that-is-long-enough"
KEY = "gsk_a-really-long-fake-key-value-12345678ABCD"

# A provider name Aura does not implement, for the tests that need a
# rejection. These tests said "deepseek" until Phase 11, which was true
# when they were written and stopped testing anything the moment DeepSeek
# was actually implemented - five tests asserting a 422 quietly became
# five tests asserting that a supported provider accepts a key. The name
# below is checked against the registry so that cannot happen silently
# again.
UNKNOWN_PROVIDER = "notaprovider"


def test_the_placeholder_provider_name_is_really_unsupported():
    from brain.router import KEYLESS_PROVIDERS, PROVIDER_KEYS

    assert UNKNOWN_PROVIDER not in PROVIDER_KEYS
    assert UNKNOWN_PROVIDER not in KEYLESS_PROVIDERS
    assert UNKNOWN_PROVIDER != "mock"


# ----------------------------------------------------------------------
# Masking - the only form a key may take in a response
# ----------------------------------------------------------------------

class TestMasking:

    def test_mask_long_key_keeps_last_four(self):
        assert mask(KEY) == "•" * 8 + "ABCD"

    def test_mask_short_key_fully_hidden(self):
        # A four-character key masked to its last four characters would be
        # the key itself. Nothing of a short key is shown.
        assert "•" in mask("1234")
        assert "1234" not in mask("1234")

    def test_mask_blank_is_empty(self):
        assert mask("") == ""
        assert mask(None) == ""
        assert mask("   ") == ""


# ----------------------------------------------------------------------
# CredentialStore - encryption, environment, reload
# ----------------------------------------------------------------------

class TestCredentialStore:

    def test_writes_are_encrypted_not_plaintext(self, tmp_path):
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        store.set("groq", KEY)
        blob = (tmp_path / "cred.enc").read_text(encoding="utf-8")
        assert KEY not in blob
        assert "groq" not in blob  # the provider name is not in the file either

    def test_reload_returns_the_key(self, tmp_path):
        path = tmp_path / "cred.enc"
        CredentialStore(path=path, secret=SECRET).set("mistral", KEY)
        reloaded = CredentialStore(path=path, secret=SECRET)
        assert reloaded.masked("mistral") == mask(KEY)
        assert reloaded.has("mistral")

    def test_store_applies_key_to_environment(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        store.set("groq", KEY)
        assert os.environ.get("GROQ_API_KEY") == KEY

    def test_delete_unsets_environment(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        store.set("groq", KEY)
        assert store.delete("groq") is True
        assert os.environ.get("GROQ_API_KEY") is None
        assert not store.has("groq")

    def test_delete_when_nothing_stored_returns_false(self, tmp_path):
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        assert store.delete("openrouter") is False

    def test_unknown_provider_rejected(self, tmp_path):
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        with pytest.raises(CredentialError):
            store.set(UNKNOWN_PROVIDER, KEY)

    def test_blank_key_rejected(self, tmp_path):
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        with pytest.raises(CredentialError):
            store.set("groq", "   ")

    def test_masked_value_not_accepted_as_key(self, tmp_path):
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        store.set("groq", KEY)
        with pytest.raises(CredentialError):
            store.set("groq", mask(KEY))

    def test_persistent_requires_a_secret(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AURA_SECRET_KEY", raising=False)
        monkeypatch.delenv("AURA_SERVER_AUTH_TOKEN", raising=False)
        store = CredentialStore(path=tmp_path / "cred.enc")
        assert store.persistent is False
        with pytest.raises(CredentialError):
            store.set("groq", KEY)
        # Nothing was written.
        assert not (tmp_path / "cred.enc").exists()

    def test_auth_token_is_a_valid_secret_source(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AURA_SECRET_KEY", raising=False)
        monkeypatch.setenv("AURA_SERVER_AUTH_TOKEN", "an-auth-token")
        store = CredentialStore(path=tmp_path / "cred.enc")
        assert store.persistent is True

    def test_corrupt_blob_does_not_raise(self, tmp_path):
        path = tmp_path / "cred.enc"
        path.write_text("not even close to json", encoding="utf-8")
        store = CredentialStore(path=path, secret=SECRET)
        assert store.masked("groq") == ""

    def test_wrong_secret_cannot_decrypt(self, tmp_path, monkeypatch):
        path = tmp_path / "cred.enc"
        CredentialStore(path=path, secret="first-secret-aaaa").set("groq", KEY)

        # `set` applied the key to the environment, which is the whole
        # point of it - but this test is about what the *store* can read
        # back, so clear that away first. Otherwise the environment
        # fallback in `masked` answers, and the assertion below would pass
        # or fail for a reason unrelated to decryption.
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        store = CredentialStore(path=path, secret="second-secret-bbbb")
        assert store.masked("groq") == ""
        assert not store.has("groq")

    def test_environment_key_seen_without_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", KEY)
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
        assert store.has("groq")
        assert store.source_of("groq") == "environment"
        assert store.masked("groq") == mask(KEY)

    def test_nothing_logs_the_key(self, tmp_path, caplog):
        """
        A key must not reach the log, on any path - including the failure
        paths, which are where a careless `logger.warning("... %s", value)`
        usually hides. Exercised together because the logging call sites
        are shared.
        """

        import logging

        with caplog.at_level(logging.DEBUG):
            store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)
            store.set("groq", KEY)
            store.masked("groq")
            store.masked_all()
            store.delete("groq")

            # The failure paths: an unreadable file and an undecryptable one.
            corrupt = tmp_path / "corrupt.enc"
            corrupt.write_text("not json", encoding="utf-8")
            CredentialStore(path=corrupt, secret=SECRET).masked("groq")

            wrong = tmp_path / "wrong.enc"
            CredentialStore(path=wrong, secret="one-secret-aaaa").set("groq", KEY)
            CredentialStore(path=wrong, secret="other-secret-bbbb").masked("groq")

        assert KEY not in caplog.text
        # Not even a fragment: the mask keeps four characters, so a
        # substring check on the rest is what proves nothing longer leaked.
        assert KEY[:-4] not in caplog.text

    def test_key_not_in_error_messages(self, tmp_path):
        """A rejected key must not be echoed back in the reason."""

        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)

        with pytest.raises(CredentialError) as exc:
            store.set(UNKNOWN_PROVIDER, KEY)

        assert KEY not in str(exc.value)


# ----------------------------------------------------------------------
# RuntimeSettings - validation, all-or-nothing, merge
# ----------------------------------------------------------------------

class TestRuntimeSettings:

    def test_update_is_all_or_nothing(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        with pytest.raises(SettingsError):
            store.update({"proactive": {"enabled": True, "max_per_day": 999}})
        assert store.overrides == {}

    def test_effective_merges_over_base(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        store.update({"proactive": {"enabled": True, "max_per_day": 3}})
        merged = store.effective({
            "proactive": {"enabled": False, "max_per_day": 4, "cooldown_seconds": 7200}
        })
        assert merged["proactive"] == {
            "enabled": True, "max_per_day": 3, "cooldown_seconds": 7200,
        }

    def test_unknown_path_rejected(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        with pytest.raises(SettingsError):
            store.update({"server": {"host": "0.0.0.0"}})

    def test_unknown_provider_rejected(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        with pytest.raises(SettingsError) as exc:
            store.update({"llm": {"provider": UNKNOWN_PROVIDER}})
        assert "gemini" in str(exc.value)  # names the real options

    def test_reset_clears_overrides(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        store.update({"proactive": {"enabled": True}})
        assert store.reset() == ["proactive.enabled"]
        assert store.overrides == {}

    def test_persists_across_reload(self, tmp_path):
        path = tmp_path / "settings.json"
        RuntimeSettings(path=path).update({"memory": {"recall": False}})
        reloaded = RuntimeSettings(path=path)
        assert reloaded.overrides == {"memory.recall": False}

    def test_structured_value_passes_validator_whole(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        accepted = store.update({
            "proactive": {"quiet_hours": [[22, 8], [13, 14]]}
        })
        assert accepted["proactive.quiet_hours"] == [[22, 8], [13, 14]]

    def test_invalid_quiet_hours_rejected(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        with pytest.raises(SettingsError):
            store.update({"proactive": {"quiet_hours": [[25, 8]]}})

    def test_boolean_coerces_from_string(self, tmp_path):
        store = RuntimeSettings(path=tmp_path / "settings.json")
        accepted = store.update({"proactive": {"enabled": "true"}})
        assert accepted["proactive.enabled"] is True


# ----------------------------------------------------------------------
# The API - auth required, keys never leak
# ----------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    """A live app with mock provider and isolated stores, plus an auth header."""

    from fastapi.testclient import TestClient
    from server import config as server_config
    from server.main import app
    from server.runtime import init_runtime, shutdown_runtime

    from core import credentials, settings_store

    # `server.config.settings` is built once, at import time, from the
    # environment. By the time this fixture runs the module is long
    # imported, so setting AURA_SERVER_AUTH_TOKEN here would change
    # nothing and every request would 401 - it only appeared to work when
    # this file ran alone and happened to import the module first. Set the
    # field on the singleton, which is what `tests/test_server.py` does.
    # `AURA_SECRET_KEY` on purpose, not the auth token: the conftest
    # fixture clears the former, and the latter is only set on the
    # settings singleton (see above), so without this the store would be
    # non-persistent and every key test would exercise only the degraded
    # path. `TestNonPersistentKeys` covers that path deliberately.
    monkeypatch.setenv("AURA_SECRET_KEY", "a-test-secret-for-the-key-store")

    previous_token = server_config.settings.auth_token
    server_config.settings.auth_token = "test-token"

    # The autouse conftest fixture already redirected the paths; these
    # make the singletons read the redirected ones at build time.
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


AUTH = {"Authorization": "Bearer test-token"}


class TestSettingsApi:

    def test_settings_requires_auth(self, api):
        response = api.get("/api/settings")
        assert response.status_code in (401, 403)

    def test_settings_returns_effective_and_overrides(self, api):
        response = api.get("/api/settings", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        assert "effective" in body
        assert "overrides" in body
        assert "configurable" in body
        # No key material. Checked against a real key rather than the word
        # "key", which legitimately appears in the persistence note and in
        # field names - `test_stored_key_never_leaks_through_any_endpoint`
        # is the one that pins the actual secret never being present.
        assert KEY not in response.text

    def test_patch_settings_applies(self, api):
        response = api.patch(
            "/api/settings",
            headers=AUTH,
            json={"settings": {"proactive": {"enabled": True, "max_per_day": 5}}},
        )
        assert response.status_code == 200
        report = response.json()
        assert "proactive.enabled" in report["applied"]
        assert report["needs_restart"] is False

    def test_patch_restart_required_path(self, api):
        response = api.patch(
            "/api/settings", headers=AUTH,
            json={"settings": {"vision": {"enabled": False}}},
        )
        assert response.status_code == 200
        report = response.json()
        assert "vision.enabled" in report["restart_required"]
        assert report["needs_restart"] is True

    def test_patch_rejects_unknown_setting(self, api):
        response = api.patch(
            "/api/settings", headers=AUTH,
            json={"settings": {"server": {"host": "0.0.0.0"}}},
        )
        assert response.status_code == 422
        # And nothing was applied.
        assert api.get("/api/settings", headers=AUTH).json()["overrides"] == {}

    def test_patch_rejects_invalid_value(self, api):
        response = api.patch(
            "/api/settings", headers=AUTH,
            json={"settings": {"proactive": {"max_per_day": 999}}},
        )
        assert response.status_code == 422
        assert "max_per_day" in response.json()["detail"]["message"]

    def test_settings_requires_auth_for_writes(self, api):
        response = api.patch("/api/settings", json={"settings": {"proactive": {"enabled": True}}})
        assert response.status_code in (401, 403)


class TestProvidersApi:

    def test_providers_requires_auth(self, api):
        assert api.get("/api/providers").status_code in (401, 403)

    def test_providers_never_return_a_key(self, api):
        response = api.get("/api/providers", headers=AUTH)
        body = json.dumps(response.json())
        assert "gsk_" not in body
        assert "sk-" not in body

    def test_put_key_then_providers_show_masked(self, api):
        put = api.put(
            "/api/providers/groq/key", headers=AUTH,
            json={"key": KEY},
        )
        assert put.status_code == 200
        assert put.json()["key_masked"] == mask(KEY)

        listed = api.get("/api/providers", headers=AUTH).json()
        groq = next(p for p in listed["providers"] if p["name"] == "groq")
        assert groq["configured"] is True
        assert groq["key_masked"] == mask(KEY)
        assert KEY not in json.dumps(groq)

    def test_put_requires_auth(self, api):
        assert api.put("/api/providers/groq/key", json={"key": KEY}).status_code in (401, 403)

    def test_put_rejects_masked_value(self, api):
        put = api.put(
            "/api/providers/groq/key", headers=AUTH, json={"key": mask(KEY)},
        )
        assert put.status_code == 422

    def test_put_rejects_unknown_provider(self, api):
        response = api.put(
            f"/api/providers/{UNKNOWN_PROVIDER}/key", headers=AUTH, json={"key": KEY},
        )
        assert response.status_code == 422

    def test_delete_key_removes_it(self, api):
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})
        deleted = api.delete("/api/providers/groq/key", headers=AUTH)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        listed = api.get("/api/providers", headers=AUTH).json()
        groq = next(p for p in listed["providers"] if p["name"] == "groq")
        assert groq["configured"] is False

    def test_health_endpoint_reports_chain(self, api):
        response = api.get("/api/providers/health", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        assert "chain" in body
        assert "in_fallback" in body

    def test_health_requires_auth(self, api):
        assert api.get("/api/providers/health").status_code in (401, 403)

    def test_test_endpoint_unknown_provider(self, api):
        response = api.post(
            "/api/providers/test", headers=AUTH,
            json={"provider": UNKNOWN_PROVIDER},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is False

    def test_stored_key_never_leaks_through_any_endpoint(self, api):
        """
        The spec's rule, checked end to end: once a key is saved, no
        endpoint returns it. Includes /api/settings, which returns the
        whole effective config and is the one most likely to acquire a
        leak later as the config tree grows.
        """

        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})

        for path in (
            "/api/settings",
            "/api/providers",
            "/api/providers/health",
        ):
            body = api.get(path, headers=AUTH).text
            assert KEY not in body, f"{path} returned the key"
            assert KEY[:-4] not in body, f"{path} returned part of the key"

        # And the live-test endpoint, which is the one route that handles
        # the real value.
        probe = api.post(
            "/api/providers/test", headers=AUTH, json={"provider": "groq"},
        )
        assert KEY not in probe.text
        assert KEY[:-4] not in probe.text

    def test_no_route_logs_the_key(self, api, caplog):
        """
        The store has its own version of this test. This one covers the
        layer above it: the routes that receive the key as a request body,
        where an added `logger.info("setting %s", body)` would leak it
        without the store being involved at all.
        """

        import logging

        with caplog.at_level(logging.DEBUG):

            api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})
            api.get("/api/providers", headers=AUTH)
            api.post("/api/providers/test", headers=AUTH, json={"provider": "groq"})
            api.patch(
                "/api/settings", headers=AUTH,
                json={"settings": {"llm": {"provider": "groq"}}},
            )
            api.delete("/api/providers/groq/key", headers=AUTH)

            # The rejection paths too, which is where a "bad key: %s"
            # would sit.
            api.put(f"/api/providers/{UNKNOWN_PROVIDER}/key", headers=AUTH, json={"key": KEY})

        assert KEY not in caplog.text
        assert KEY[:-4] not in caplog.text


class TestResetApi:

    def test_reset_reverts_settings(self, api):
        api.patch(
            "/api/settings", headers=AUTH,
            json={"settings": {"proactive": {"enabled": True}}},
        )
        response = api.post("/api/settings/reset", headers=AUTH, json={})
        assert response.status_code == 200
        assert "proactive.enabled" in response.json()["reset"]
        assert api.get("/api/settings", headers=AUTH).json()["overrides"] == {}

    def test_reset_does_not_touch_keys(self, api):
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})
        api.post("/api/settings/reset", headers=AUTH, json={})
        listed = api.get("/api/providers", headers=AUTH).json()
        groq = next(p for p in listed["providers"] if p["name"] == "groq")
        assert groq["configured"] is True


class TestKeyReachesProviders:
    """
    The point of the feature: a key set from the phone is one the router
    can actually build a provider with. Providers read `os.getenv` in
    `__init__`, so "saved" has to mean "in the environment".
    """

    def test_saved_key_is_in_the_environment(self, api):
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})
        assert os.environ.get("GROQ_API_KEY") == KEY

    def test_deleted_key_leaves_the_environment(self, api):
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})
        api.delete("/api/providers/groq/key", headers=AUTH)
        assert not os.environ.get("GROQ_API_KEY")

    def test_key_survives_a_new_store_in_the_same_process(self, api):
        """A restart reads the blob back: same masked value, still usable."""

        api.put("/api/providers/mistral/key", headers=AUTH, json={"key": KEY})

        from core import credentials

        rebuilt = credentials.CredentialStore()

        assert rebuilt.masked("mistral") == mask(KEY)
        assert rebuilt.source_of("mistral") == "store"


class TestNonPersistentKeys:
    """
    No secret on the server: keys work for this process and the response
    says so. The spec forbids a plaintext fallback, so the alternative to
    this honest degradation is refusing the write entirely.
    """

    @pytest.fixture
    def no_secret_api(self, api, monkeypatch):

        from core import credentials

        monkeypatch.delenv("AURA_SECRET_KEY", raising=False)
        monkeypatch.delenv("AURA_SERVER_AUTH_TOKEN", raising=False)

        credentials.set_credential_store(credentials.CredentialStore())

        yield api

        credentials.set_credential_store(None)

    def test_put_reports_not_persistent_but_saved(self, no_secret_api):
        response = no_secret_api.put(
            "/api/providers/groq/key", headers=AUTH, json={"key": KEY},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["saved"] is True
        assert body["persistent"] is False
        assert "AURA_SECRET_KEY" in body["warning"]
        assert body["key_masked"] == mask(KEY)

    def test_key_still_reaches_the_environment(self, no_secret_api):
        no_secret_api.put(
            "/api/providers/groq/key", headers=AUTH, json={"key": KEY},
        )
        assert os.environ.get("GROQ_API_KEY") == KEY

    def test_nothing_was_written_to_disk(self, no_secret_api):
        no_secret_api.put(
            "/api/providers/groq/key", headers=AUTH, json={"key": KEY},
        )
        from core.credentials import get_credential_store
        assert not get_credential_store().path.exists()

    def test_invalid_key_is_still_rejected(self, no_secret_api):
        """
        The bug this pins: a non-persistent store raises for a masked
        value too, and inferring "could not persist" from `persistent`
        turned that rejection into a 200 "saved".
        """

        response = no_secret_api.put(
            "/api/providers/groq/key", headers=AUTH, json={"key": mask(KEY)},
        )
        assert response.status_code == 422

    def test_unknown_provider_is_still_rejected(self, no_secret_api):
        response = no_secret_api.put(
            f"/api/providers/{UNKNOWN_PROVIDER}/key", headers=AUTH, json={"key": KEY},
        )
        assert response.status_code == 422


# ----------------------------------------------------------------------
# Rotation - replacing a key, which is the common case on a live deploy
# ----------------------------------------------------------------------

ROTATED = "gsk_a-second-fake-key-value-0987654321WXYZ"


class TestKeyRotation:
    """
    The spec asks for "update key" as its own case, and it is not the same
    code path as first-time storage: the store already holds a value, the
    environment already holds one, and the old key has to stop being
    either. A rotation that appended instead of replacing would leave the
    dead key in the environment, where providers read from.
    """

    def test_store_replaces_rather_than_keeps_both(self, tmp_path):
        store = CredentialStore(path=tmp_path / "cred.enc", secret=SECRET)

        store.set("groq", KEY)
        store.set("groq", ROTATED)

        assert store.masked("groq") == mask(ROTATED)
        assert store.secret_for("groq") == ROTATED
        assert os.environ.get("GROQ_API_KEY") == ROTATED

    def test_rotation_survives_a_reload(self, tmp_path, monkeypatch):
        path = tmp_path / "cred.enc"

        first = CredentialStore(path=path, secret=SECRET)
        first.set("groq", KEY)
        first.set("groq", ROTATED)

        # `set` put the value in the environment; clear it so the reloaded
        # store answers from its own blob rather than from that fallback.
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        reloaded = CredentialStore(path=path, secret=SECRET)

        assert reloaded.masked("groq") == mask(ROTATED)
        assert reloaded.secret_for("groq") == ROTATED

    def test_the_old_key_is_not_on_disk_afterwards(self, tmp_path):
        path = tmp_path / "cred.enc"
        store = CredentialStore(path=path, secret=SECRET)

        store.set("groq", KEY)
        store.set("groq", ROTATED)

        blob = path.read_text(encoding="utf-8")

        # Both, for the same reason: the file is encrypted, so this is a
        # regression guard against a future plaintext or partially
        # plaintext format rather than a test of the cipher.
        assert KEY not in blob
        assert ROTATED not in blob

    def test_api_rotation_reports_only_the_new_mask(self, api):
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})

        second = api.put(
            "/api/providers/groq/key", headers=AUTH, json={"key": ROTATED},
        )

        assert second.status_code == 200
        assert second.json()["key_masked"] == mask(ROTATED)

        listed = api.get("/api/providers", headers=AUTH).json()
        groq = next(p for p in listed["providers"] if p["name"] == "groq")

        assert groq["configured"] is True
        assert groq["key_masked"] == mask(ROTATED)

        # Neither key in the body, and no fragment of either.
        body = json.dumps(listed)
        for value in (KEY, ROTATED):
            assert value not in body
            assert value[:-4] not in body

    def test_api_rotation_reaches_the_providers(self, api):
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": ROTATED})

        assert os.environ.get("GROQ_API_KEY") == ROTATED

    def test_a_masked_value_does_not_overwrite_a_real_key(self, api):
        """
        The realistic accident: the phone shows the mask, and a UI that
        submitted what it displayed would replace a working key with
        bullet characters. Rejected, and the stored key is untouched.
        """

        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})

        attempt = api.put(
            "/api/providers/groq/key", headers=AUTH, json={"key": mask(KEY)},
        )

        assert attempt.status_code == 422
        assert os.environ.get("GROQ_API_KEY") == KEY

        listed = api.get("/api/providers", headers=AUTH).json()
        groq = next(p for p in listed["providers"] if p["name"] == "groq")
        assert groq["key_masked"] == mask(KEY)


# ----------------------------------------------------------------------
# The attacker case - STEP 18, checked by effect and not by status code
# ----------------------------------------------------------------------

class TestUnauthenticatedCallerChangesNothing:
    """
    The spec's hardest requirement on these routes: "A remote attacker must
    not be able to call PATCH /api/settings and insert their own API key or
    change Aura's provider."

    `TestSettingsApi` already pins the status codes. These pin the *effect*,
    which is the part that would survive a refactor going wrong: a
    dependency dropped from one decorator turns a 401 into a 200, and a
    test that only reads `response.status_code` on the routes it happens to
    remember would not notice on the others.
    """

    def test_patch_cannot_change_the_provider(self, api):
        before = api.get("/api/settings", headers=AUTH).json()

        # A *valid* provider on purpose. If the payload were invalid the
        # 422 would prove nothing about authentication.
        attempt = api.patch(
            "/api/settings",
            json={"settings": {"llm": {"provider": "mistral"}}},
        )

        assert attempt.status_code in (401, 403)

        after = api.get("/api/settings", headers=AUTH).json()

        assert after["overrides"] == before["overrides"]
        assert (
            after["effective"]["llm"]["provider"]
            == before["effective"]["llm"]["provider"]
        )

    def test_a_wrong_token_is_refused_as_well(self, api):
        attempt = api.patch(
            "/api/settings",
            headers={"Authorization": "Bearer not-the-token"},
            json={"settings": {"proactive": {"enabled": True}}},
        )

        assert attempt.status_code in (401, 403)
        assert api.get("/api/settings", headers=AUTH).json()["overrides"] == {}

    def test_a_key_cannot_be_installed_without_a_token(self, api, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        attempt = api.put("/api/providers/groq/key", json={"key": KEY})

        assert attempt.status_code in (401, 403)

        # The two places a successful write would show up.
        assert not os.environ.get("GROQ_API_KEY")

        listed = api.get("/api/providers", headers=AUTH).json()
        groq = next(p for p in listed["providers"] if p["name"] == "groq")
        assert groq["configured"] is False

    def test_a_key_cannot_be_deleted_without_a_token(self, api):
        api.put("/api/providers/groq/key", headers=AUTH, json={"key": KEY})

        assert api.delete("/api/providers/groq/key").status_code in (401, 403)

        assert os.environ.get("GROQ_API_KEY") == KEY

        listed = api.get("/api/providers", headers=AUTH).json()
        groq = next(p for p in listed["providers"] if p["name"] == "groq")
        assert groq["configured"] is True

    def test_settings_cannot_be_reset_without_a_token(self, api):
        api.patch(
            "/api/settings", headers=AUTH,
            json={"settings": {"proactive": {"max_per_day": 3}}},
        )

        assert api.post("/api/settings/reset", json={}).status_code in (401, 403)

        # The overlay is keyed by the flat dotted paths the validator uses.
        overrides = api.get("/api/settings", headers=AUTH).json()["overrides"]
        assert overrides["proactive.max_per_day"] == 3

    def test_a_provider_probe_cannot_be_triggered_without_a_token(self, api):
        """
        Not a settings write, but it spends the deployment's own key on a
        live call. An open route here is a free oracle for whether a key is
        valid, paid for by the person who deployed Aura.
        """

        attempt = api.post("/api/providers/test", json={"provider": "groq"})

        assert attempt.status_code in (401, 403)

    def test_every_settings_and_provider_route_requires_a_token(self, api):
        """
        Enumerated from the app rather than listed here, so a route added to
        `server/routes/settings.py` without `Depends(verify_token)` fails
        this test on the day it is written instead of on the day it is
        exploited.
        """

        from server.main import app

        checked = 0

        for route in app.routes:

            path = getattr(route, "path", "")

            if not path.startswith(("/api/settings", "/api/providers")):
                continue

            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:

                # The only templated segment in this router.
                target = path.replace("{provider}", "groq")

                response = api.request(method, target, json={})

                assert response.status_code in (401, 403), (
                    f"{method} {target} answered {response.status_code} "
                    "without a token"
                )

                checked += 1

        # Guards the guard: an empty loop would pass silently.
        assert checked >= 6, f"only {checked} routes were checked"

    def test_no_settable_path_is_credential_material(self, api):
        """
        Keys are not settings. The allow-list is what a caller with a valid
        token may write, so nothing in it may be a place to park a secret -
        `PUT /api/providers/{provider}/key` is the only way in, and it
        stores encrypted and returns a mask.
        """

        from core.settings_store import ALLOWED

        # The last segment, matched exactly. A substring test would call
        # `llm.max_output_tokens` a credential, and a check that cries wolf
        # is one someone eventually deletes.
        forbidden = {
            "key", "api_key", "apikey", "token", "auth_token",
            "secret", "secret_key", "password", "credential", "credentials",
        }

        offenders = [
            path for path in ALLOWED
            if path.rsplit(".", 1)[-1].lower() in forbidden
        ]

        assert offenders == []

        # And the attempt is refused by name rather than quietly ignored.
        attempt = api.patch(
            "/api/settings", headers=AUTH,
            json={"settings": {"llm": {"api_key": KEY}}},
        )

        assert attempt.status_code == 422
        assert KEY not in attempt.text
        assert api.get("/api/settings", headers=AUTH).json()["overrides"] == {}
