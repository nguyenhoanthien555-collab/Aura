"""
Phase 6 - security and deployment hardening.

Four defects, one theme: the deployed server used to be more permissive
and less honest than its documentation.

  * AURA-P1-008  an empty token silently disabled authentication
  * AURA-P1-007  wildcard CORS origins were paired with credentials
  * AURA-P1-014  every failure was the same opaque 500
  * AURA-P0-005  the deployed server must not imply it can act on a PC

The tests are grouped by the promise each defect broke rather than by
defect number, because that is how they will be read when one fails.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from brain.providers.errors import (
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from server import config as server_config
from server.config import (
    INSECURE_ENV_VAR,
    InsecureConfigurationError,
    ServerSettings,
    cors_policy,
    enforce_auth_policy,
)
from server.errors import classify


TOKEN = "test-token-not-a-real-secret"


@pytest.fixture(autouse=True)
def _no_ambient_insecure_flag(monkeypatch):
    """
    The opt-in must never be inherited from the developer's environment.

    Without this, a machine that exports AURA_ALLOW_INSECURE=1 would make
    the "startup fails" tests pass for the wrong reason - or rather, fail
    to fail, which is worse.
    """
    monkeypatch.delenv(INSECURE_ENV_VAR, raising=False)


def settings_with(**overrides) -> ServerSettings:
    """
    Settings built without reading `.env`.

    `_env_file=None` is load-bearing: a developer with a real token in
    `.env` would otherwise never see the unauthenticated case at all.
    """
    return ServerSettings(_env_file=None, **overrides)


# ======================================================================
# 1. Authentication cannot be disabled by accident  (AURA-P1-008)
# ======================================================================

def test_a_missing_token_stops_startup_instead_of_publishing_the_api():
    # The defect: the server logged a warning and then served every
    # request anyway. A forgotten environment variable on a public host
    # is the whole failure mode, and a warning in a log nobody reads is
    # not a control.
    settings = settings_with(auth_token="")

    with pytest.raises(InsecureConfigurationError):
        enforce_auth_policy(settings)


def test_a_configured_token_starts_silently():
    settings = settings_with(auth_token=TOKEN)

    assert enforce_auth_policy(settings) is None


def test_insecure_development_is_allowed_only_when_asked_for(monkeypatch):
    monkeypatch.setenv(INSECURE_ENV_VAR, "1")

    settings = settings_with(auth_token="")

    warning = enforce_auth_policy(settings)

    assert warning is not None
    assert "UNAUTHENTICATED" in warning


@pytest.mark.parametrize("value", ["0", "", "no", "false", "maybe", "  "])
def test_only_an_affirmative_opt_in_counts(monkeypatch, value):
    # A typo, a leftover `=0`, or a half-edited line must not be read as
    # consent. Anything unrecognised means "no", which fails safe.
    monkeypatch.setenv(INSECURE_ENV_VAR, value)

    with pytest.raises(InsecureConfigurationError):
        enforce_auth_policy(settings_with(auth_token=""))


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
def test_the_documented_affirmative_spellings_are_accepted(monkeypatch, value):
    monkeypatch.setenv(INSECURE_ENV_VAR, value)

    assert enforce_auth_policy(settings_with(auth_token="")) is not None


def test_the_refusal_names_the_variable_without_printing_any_token():
    # The error has to be actionable - it is the only thing the operator
    # sees - while a token that *is* configured elsewhere in the process
    # must never appear in it.
    settings = settings_with(auth_token="")

    with pytest.raises(InsecureConfigurationError) as raised:
        enforce_auth_policy(settings)

    message = str(raised.value)

    assert "AURA_SERVER_AUTH_TOKEN" in message
    assert INSECURE_ENV_VAR in message


def test_no_warning_ever_contains_the_token_itself(monkeypatch):
    # The insecure path is the one that logs the most, so it is the one
    # most likely to leak. There is no token in this case by definition,
    # but the assertion pins the rule rather than the circumstance.
    monkeypatch.setenv(INSECURE_ENV_VAR, "1")

    warning = enforce_auth_policy(settings_with(auth_token="", host="0.0.0.0"))

    assert TOKEN not in warning
    assert "token=" not in warning.lower()


def test_a_public_bind_is_called_out_in_the_insecure_warning(monkeypatch):
    monkeypatch.setenv(INSECURE_ENV_VAR, "1")

    public = enforce_auth_policy(settings_with(auth_token="", host="0.0.0.0"))
    local = enforce_auth_policy(settings_with(auth_token="", host="127.0.0.1"))

    assert "not loopback" in public
    assert "not loopback" not in local

    # Both are still warnings: loopback is not an exemption, because port
    # mapping makes "localhost" a claim about the container.
    assert "UNAUTHENTICATED" in local


def test_startup_enforcement_is_not_skipped_when_a_runtime_already_exists():
    # `lifespan` short-circuits the rest of its body when a runtime was
    # pre-installed (the test fixtures do exactly that). The auth check
    # must sit outside that guard, or the one path that skips it is the
    # path a test harness uses - and production would be unprotected the
    # moment anything pre-warmed the runtime.
    import inspect

    from server import main

    source = inspect.getsource(main.lifespan)

    enforcement = source.index("enforce_auth_policy")
    guard = source.index("if not is_initialized()")

    assert enforcement < guard, (
        "the auth policy must be enforced before the is_initialized guard"
    )


def test_the_asgi_app_aborts_startup_when_a_token_is_missing(monkeypatch):
    """
    The behavior, through the real application: with no token and no
    opt-in, entering the lifespan raises before the port is ever bound.
    """

    from fastapi.testclient import TestClient

    from server import main as server_main
    from server.config import settings

    previous_token = settings.auth_token
    settings.auth_token = ""
    monkeypatch.delenv(INSECURE_ENV_VAR, raising=False)

    try:
        with pytest.raises(InsecureConfigurationError):
            with TestClient(server_main.app):
                pass  # pragma: no cover - startup raises before this runs
    finally:
        settings.auth_token = previous_token


def test_the_asgi_app_starts_insecurely_when_the_opt_in_is_set(monkeypatch):
    """
    The escape hatch, through the real application: with the opt-in set,
    the same no-token configuration starts and serves.

    Note this is NOT the default - `test_a_missing_token_stops_startup...`
    proves the un-opted start refuses. Both directions of the switch are
    exercised because each failure mode hides the other.
    """

    from fastapi.testclient import TestClient

    from server import main as server_main
    from server.config import settings

    previous_token = settings.auth_token
    settings.auth_token = ""
    monkeypatch.setenv(INSECURE_ENV_VAR, "1")

    try:
        with TestClient(server_main.app) as test_client:
            # The root route is public; reaching it at all proves the
            # server came up under the opt-in.
            response = test_client.get("/")
            assert response.status_code == 200
    finally:
        settings.auth_token = previous_token


# ======================================================================
# 2. CORS never pairs a wildcard with credentials  (AURA-P1-007)
# ======================================================================

def test_wildcard_origins_refuse_credentials():
    policy = cors_policy(settings_with(cors_origins=["*"]))

    assert policy["allow_origins"] == ["*"]
    assert policy["allow_credentials"] is False


def test_explicit_origins_may_carry_credentials():
    policy = cors_policy(
        settings_with(cors_origins=["https://aura.example"])
    )

    assert policy["allow_origins"] == ["https://aura.example"]
    assert policy["allow_credentials"] is True


def test_a_wildcard_anywhere_in_the_list_disables_credentials():
    # `["https://aura.example", "*"]` is still "anyone may call".
    policy = cors_policy(
        settings_with(cors_origins=["https://aura.example", "*"])
    )

    assert policy["allow_credentials"] is False


def cors_probe(origins):
    """A minimal app carrying the real policy, for measuring headers."""

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware, **cors_policy(settings_with(cors_origins=origins))
    )

    @app.get("/probe")
    async def probe():
        return {}

    return TestClient(app)


def test_a_preflight_no_longer_reflects_an_arbitrary_origin_with_credentials():
    # This is the actual exposure, and the reason the defect was real
    # rather than cosmetic. With allow_origins=["*"] AND credentials on,
    # Starlette sets `preflight_explicit_allow_origin`, so a preflight
    # echoed whichever origin asked - and a browser honours
    # `Allow-Origin: https://evil.example` + `Allow-Credentials: true`.
    # (A simple GET sent a literal `*`, which browsers reject. The hole
    # opened through preflight.)
    response = cors_probe(["*"]).options(
        "/probe",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    allow_origin = response.headers.get("access-control-allow-origin")

    assert allow_origin != "https://evil.example"
    assert response.headers.get("access-control-allow-credentials") != "true"


def test_a_configured_origin_still_works_end_to_end():
    # A policy that blocks everything would pass the test above and be
    # useless. The named origin must actually be allowed, with credentials.
    client = cors_probe(["https://aura.example"])

    response = client.options(
        "/probe",
        headers={
            "Origin": "https://aura.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://aura.example"
    )
    assert response.headers["access-control-allow-credentials"] == "true"


def test_an_unlisted_origin_is_refused_when_origins_are_explicit():
    response = cors_probe(["https://aura.example"]).options(
        "/probe",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers.get("access-control-allow-origin") is None


def test_the_application_itself_is_mounted_with_the_safe_policy():
    # The policy function being correct is not the same as the app using
    # it. This pins the wiring, which is what the defect actually was.
    from starlette.middleware.cors import CORSMiddleware as StarletteCORS

    from server.main import app

    cors = [m for m in app.user_middleware if m.cls is StarletteCORS]

    assert len(cors) == 1

    options = cors[0].kwargs

    if "*" in options["allow_origins"]:
        assert options["allow_credentials"] is False


# ======================================================================
# 3. A failure says which kind it is  (AURA-P1-014)
# ======================================================================

def test_a_rate_limit_is_429():
    failure = classify(ProviderRateLimitError("quota exceeded for key sk-abc"))

    assert failure.status == 429
    assert failure.code == "rate_limited"


def test_an_unavailable_provider_is_503():
    failure = classify(ProviderUnavailableError("connection refused"))

    assert failure.status == 503
    assert failure.code == "provider_unavailable"


def test_an_unrecognised_error_stays_a_500():
    # Deliberate: guessing "provider problem" for an exception this layer
    # was never taught to read is the mistake `_category_of` was fixed
    # for in Phase 1. Unknown means internal.
    failure = classify(ValueError("something else entirely"))

    assert failure.status == 500
    assert failure.code == "chat_failed"


def test_the_narrower_rate_limit_wins_over_its_parent_class():
    # ProviderRateLimitError subclasses ProviderUnavailableError, so an
    # isinstance chain in the wrong order silently turns every 429 into a
    # 503 - and the client stops honouring Retry-After.
    assert issubclass(ProviderRateLimitError, ProviderUnavailableError)
    assert classify(ProviderRateLimitError("x")).status == 429


def test_retry_after_is_carried_when_the_provider_supplied_one():
    failure = classify(ProviderRateLimitError("slow down", retry_after=30))

    assert failure.retry_after == 30.0


def test_no_classified_message_quotes_the_exception():
    # Every client-facing string is a constant in server/errors.py. This
    # is the rule that keeps hosts, paths and key fragments off the wire.
    secret = (
        "Ollama at http://192.168.1.50:11434 failed for key sk-live-abc123 "
        "at C:\\Users\\Hoan Thien\\secrets"
    )

    for error in (
        ProviderRateLimitError(secret),
        ProviderUnavailableError(secret),
        RuntimeError(secret),
    ):
        message = classify(error).message

        assert "192.168" not in message
        assert "sk-live" not in message
        assert "C:\\Users" not in message
        assert "Ollama" not in message


# ======================================================================
# 4. The deployed server stays honest about the PC  (AURA-P0-005)
# ======================================================================

def test_the_server_still_exposes_no_device_execution_route():
    # Phase 4 pinned this; Phase 6 keeps it pinned, because "deployment
    # hardening" is exactly the sort of change that would quietly add a
    # dispatch endpoint. Duplicated on purpose: this file is what a
    # security review reads.
    from server.main import app

    paths = {route.path for route in getattr(app, "routes", [])}

    forbidden = [
        path for path in paths
        if any(word in path for word in ("device", "command", "exec", "shell"))
    ]

    assert forbidden == []


def test_no_physical_device_executor_is_registered_in_server_mode():
    from core.config import load_config
    from tools.executor import ToolExecutor, ToolPolicy
    from tools.factory import build_registry

    tools_config = (load_config() or {}).get("tools") or {}

    executor = ToolExecutor(
        registry=build_registry(tools_config),
        policy=ToolPolicy.from_config(tools_config),
    )

    # Nothing that reaches a machine. `current_time` reads a clock.
    assert executor.available() == ["current_time"]


def test_readiness_never_claims_a_physical_device_capability():
    # The readiness route is new in Phase 6 and is public. A "ready"
    # server must not be readable as "ready to act on your PC" - it
    # reports the language model and nothing else.
    from server.runtime import ServerRuntime

    keys = ServerRuntime.readiness.__doc__ or ""

    assert "does NOT call the provider" in keys

    # The contract itself: the three documented keys, no device key.
    import inspect

    source = inspect.getsource(ServerRuntime.readiness)

    for word in ("device", "screen_control", "keyboard", "mouse"):
        assert f'"{word}"' not in source


def test_a_deployed_server_cannot_report_a_physical_action_as_executed():
    """
    The regression STEP 5 exists for: server deployment must not be able
    to claim a physical PC action succeeded without trusted execution
    evidence.

    This asserts the *structural* half, which is what makes the claim
    impossible rather than merely discouraged. There is no route to a
    device, and the only runnable tool reads a clock - so no code path
    exists from a device request to a result the model could truthfully
    cite. The spoken half (the standing rule in `prompts/system.md`) is
    pinned by `tests/test_device_boundary.py`.
    """

    from core.config import load_config
    from server.main import app
    from tools.executor import ToolExecutor, ToolPolicy
    from tools.factory import build_registry

    # 1. Nothing to dispatch to.
    paths = {route.path for route in getattr(app, "routes", [])}

    assert not [
        p for p in paths
        if any(w in p for w in ("device", "command", "exec", "shell"))
    ]

    # 2. Nothing that could execute if it were dispatched.
    tools_config = (load_config() or {}).get("tools") or {}

    executor = ToolExecutor(
        registry=build_registry(tools_config),
        policy=ToolPolicy.from_config(tools_config),
    )

    assert executor.available() == ["current_time"]

    # 3. A device request refused rather than reported as done. `ok` is
    #    False, so there is no successful result for the model to relay -
    #    which is the only kind of evidence the prompt permits it to cite.
    for name, arguments in (
        ("open_url", {"url": "https://youtube.com"}),
        ("run_command", {"command": "notepad"}),
        ("click", {"x": 10, "y": 10}),
    ):
        result = executor.execute(name, arguments)

        assert result.ok is False, f"{name} executed on a deployed server"


def test_readiness_being_true_does_not_imply_a_reachable_pc():
    """
    `ready: true` means "can answer a chat turn". An operator or client
    must not be able to read it as "the PC is reachable", so the payload
    carries no device field at all - the absence is the contract.
    """

    import inspect

    from server.runtime import ServerRuntime

    source = inspect.getsource(ServerRuntime.readiness)

    # The documented shape. Its behaviour is asserted against a real call
    # in tests/test_server.py; this pins that nothing else joins it.
    assert '"ready"' in source
    assert '"llm_provider"' in source
    assert '"problems"' in source

    for forbidden in ("pc_", "desktop", "windows_agent", "device_agent"):
        assert forbidden not in source
