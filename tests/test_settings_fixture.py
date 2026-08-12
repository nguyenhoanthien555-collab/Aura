"""
The Android app's fixtures, kept honest against the routes that made them.

`android/app/src/test/resources/live/*.json` are the exact bodies
`server/routes/settings.py` produced, and `SettingsContractTest` parses them
with the app's own DTOs. That is the strongest contract test available - a
payload nobody retyped - and it decays the moment the server's shape moves,
because the Kotlin side has no way to notice.

So this test compares the checked-in fixtures with what the routes answer
now. It compares *shape*, not values: `key_masked`, `configured` and the
provider chain all depend on which keys a host happens to have, and pinning
those would fail on a developer's machine for no reason. `configurable` and
the provider to model-setting map are compared exactly, because those two
are what the phone locks its controls and its model picker on.

To adopt a deliberate server change:

    AURA_WRITE_ANDROID_FIXTURES=1 .venv/Scripts/python.exe -m pytest \\
        tests/test_settings_fixture.py

then run the Android suite, which will fail if the new shape has broken a
DTO rather than merely moved.
"""

import json
import os
from pathlib import Path

from tests.test_settings_api import api, AUTH  # noqa: F401


FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "android" / "app" / "src" / "test" / "resources" / "live"
)

ROUTES = {
    "settings": "/api/settings",
    "providers": "/api/providers",
    "provider_health": "/api/providers/health",
}


def _shape(value, path=""):
    """Every key path in a document, with list indices elided.

    `providers[0].name` and `providers[7].name` are the same fact about the
    contract, so both arrive as `providers[].name`. A field added or removed
    anywhere shows up here; a value changing does not.
    """

    paths = set()

    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{path}.{key}" if path else key
            paths.add(here)
            paths |= _shape(child, here)
    elif isinstance(value, list):
        for child in value:
            paths |= _shape(child, f"{path}[]")

    return paths


def _write(name, document):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _live(api, name):  # noqa: F811
    response = api.get(ROUTES[name], headers=AUTH)
    assert response.status_code == 200, f"{ROUTES[name]} answered {response.status_code}"
    return response.json()


def test_fixtures_match_the_routes(api):  # noqa: F811
    """The Android fixtures are the current server's own output."""

    live = {name: _live(api, name) for name in ROUTES}

    if os.getenv("AURA_WRITE_ANDROID_FIXTURES"):
        for name, document in live.items():
            _write(name, document)

    for name, document in live.items():

        path = FIXTURES / f"{name}.json"
        assert path.exists(), f"{path} is missing - regenerate it (see this module's docstring)"

        stored = json.loads(path.read_text(encoding="utf-8"))

        missing = _shape(document) - _shape(stored)
        extra = _shape(stored) - _shape(document)

        assert not missing, (
            f"{name}.json is missing fields the server now sends: {sorted(missing)}"
        )
        assert not extra, (
            f"{name}.json has fields the server no longer sends: {sorted(extra)}"
        )


def test_the_configurable_allow_list_is_the_one_the_phone_locks_on(api):  # noqa: F811
    """`configurable` decides which controls the hub offers at all."""

    live = _live(api, "settings")
    stored = json.loads((FIXTURES / "settings.json").read_text(encoding="utf-8"))

    assert stored["configurable"] == live["configurable"]

    # The capability-granting paths stay out of it. A bearer token must not be
    # able to widen what the tools may reach.
    for path in ("tools.allowed", "tools.allowed_paths", "tools.applications"):
        assert path not in live["configurable"]


def test_every_provider_still_names_the_setting_holding_its_model(api):  # noqa: F811
    """The mapping the Android model picker writes through."""

    live = _live(api, "providers")
    stored = json.loads((FIXTURES / "providers.json").read_text(encoding="utf-8"))

    def mapping(document):
        return {p["name"]: p["model_setting"] for p in document["providers"]}

    assert mapping(stored) == mapping(live)

    # Only Gemini reads `llm.model`. Any other provider pointed at it would be
    # a model picker that appears to work and cannot.
    for name, setting in mapping(live).items():
        if setting == "llm.model":
            assert name == "gemini", f"{name} must not be sent to llm.model"


def test_the_fixtures_contain_nothing_key_shaped():
    """These are checked in, so they are read as a leak the moment one is."""

    for name in ROUTES:

        raw = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")

        for prefix in ("AIza", "gsk_", "sk-", "xai-", "csk-", "sk_live"):
            assert prefix not in raw, f"{name}.json contains {prefix}"

        # A mask is the only key-shaped thing allowed, and only as bullets.
        document = json.loads(raw)
        for provider in document.get("providers", []) if name == "providers" else []:
            assert provider["key_masked"] == "" or set(provider["key_masked"][:8]) == {"•"}
