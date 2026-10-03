"""
Unit tests for ProviderCooldowns and FallbackProvider cooldown integration.
Verifies suppression of request storms, recovery on credential fingerprint change,
time-based expiration, and primary retry behavior.
"""

from unittest.mock import MagicMock, patch
import pytest

from brain.providers.cooldown import (
    AUTH_COOLDOWN_S,
    ACCOUNT_LIMIT_COOLDOWN_S,
    RATE_LIMIT_DEFAULT_S,
    ProviderCooldowns,
)
from brain.providers.errors import (
    AUTH_FORBIDDEN,
    AUTH_INVALID,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from brain.providers.fallback import FallbackProvider


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.current = start

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class DummyProvider:
    def __init__(self, name: str, fp: str = ""):
        self.provider_name = name
        self._fp = fp
        self.call_count = 0
        self.stream_count = 0
        self.tools_count = 0

    def credential_fingerprint(self) -> str:
        return self._fp

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        return f"{self.provider_name}: {prompt}"

    def stream(self, prompt: str):
        self.stream_count += 1
        yield f"{self.provider_name}: {prompt}"

    def generate_with_tools(self, system: str, messages: list, tools: list):
        self.tools_count += 1
        return {"content": f"{self.provider_name} tools"}


def test_auth_error_triggers_cooldown_and_skips_subsequent_calls():
    """Auth error on p1 triggers 30 min cooldown; next call skips p1 and calls p2 directly."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("primary", fp="sha256:111111111111")
    p2 = DummyProvider("secondary")

    def p1_fail(prompt):
        p1.call_count += 1
        raise ProviderAuthError("Forbidden", reason=AUTH_FORBIDDEN, http_status=403, detail="http_403")

    p1.generate = p1_fail

    fallback = FallbackProvider([p1, p2], "primary->secondary", cooldowns=cooldowns)

    # First request: p1 fails, falls back to p2
    res1 = fallback.generate("hello 1")
    assert res1 == "secondary: hello 1"
    assert p1.call_count == 1
    assert p2.call_count == 1

    # Second request: p1 is in cooldown, only p2 is called
    res2 = fallback.generate("hello 2")
    assert res2 == "secondary: hello 2"
    assert p1.call_count == 1  # p1 was skipped!
    assert p2.call_count == 2


def test_fingerprint_change_clears_cooldown_immediately():
    """Updating credentials changes fingerprint and clears cooldown on the next request."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("primary", fp="sha256:old_fingerprint")
    p2 = DummyProvider("secondary")

    p1_calls = 0

    def p1_behavior(prompt):
        nonlocal p1_calls
        p1_calls += 1
        if p1._fp == "sha256:old_fingerprint":
            raise ProviderAuthError("Unauthorized", reason=AUTH_INVALID, http_status=401)
        return "primary: recovered!"

    p1.generate = p1_behavior

    fallback = FallbackProvider([p1, p2], "primary->secondary", cooldowns=cooldowns)

    # First request triggers cooldown
    res1 = fallback.generate("test")
    assert res1 == "secondary: test"
    assert p1_calls == 1

    # Cooldown is active
    entry = cooldowns.check("primary", p1.credential_fingerprint())
    assert entry is not None
    assert entry.reason == AUTH_INVALID

    # User updates token -> fingerprint changes
    p1._fp = "sha256:new_fingerprint"

    # Second request: p1 called again because fingerprint changed!
    res2 = fallback.generate("test 2")
    assert res2 == "primary: recovered!"
    assert p1_calls == 2
    assert cooldowns.check("primary", p1.credential_fingerprint()) is None


def test_cooldown_expires_after_duration():
    """Cooldown expires when clock advances past the duration."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("primary", fp="sha256:token")
    p2 = DummyProvider("secondary")

    p1_calls = 0

    def p1_behavior(prompt):
        nonlocal p1_calls
        p1_calls += 1
        if p1_calls == 1:
            raise ProviderAuthError("Forbidden", reason=AUTH_FORBIDDEN)
        return "primary: recovered after 30 min!"

    p1.generate = p1_behavior

    fallback = FallbackProvider([p1, p2], "primary->secondary", cooldowns=cooldowns)

    # First call: p1 fails
    fallback.generate("req1")
    assert p1_calls == 1

    # 10 minutes pass: still cooling down
    clock.advance(600)
    fallback.generate("req2")
    assert p1_calls == 1

    # Advance 21 more minutes (total 31 min > 30 min)
    clock.advance(1260)
    res = fallback.generate("req3")
    assert res == "primary: recovered after 30 min!"
    assert p1_calls == 2


def test_rate_limit_retry_after_cooldown():
    """Rate limit with Retry-After sets clamped cooldown duration."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("primary")
    p2 = DummyProvider("secondary")

    p1_calls = 0

    def p1_behavior(prompt):
        nonlocal p1_calls
        p1_calls += 1
        if p1_calls == 1:
            raise ProviderRateLimitError("Rate limited", retry_after=10.0)
        return "primary: back online"

    p1.generate = p1_behavior

    fallback = FallbackProvider([p1, p2], "primary->secondary", cooldowns=cooldowns)

    fallback.generate("call 1")
    assert p1_calls == 1

    # 5 seconds pass: still in cooldown
    clock.advance(5)
    fallback.generate("call 2")
    assert p1_calls == 1

    # 6 more seconds pass (total 11 > 10s)
    clock.advance(6)
    res = fallback.generate("call 3")
    assert res == "primary: back online"
    assert p1_calls == 2


def test_account_quota_limit_cooldown_and_stop():
    """Account-level limit triggers 60m cooldown and halts failover."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("primary")
    p2 = DummyProvider("secondary")

    def p1_fail(prompt):
        raise ProviderRateLimitError("Daily quota exceeded", is_account_limit=True)

    p1.generate = p1_fail

    fallback = FallbackProvider([p1, p2], "primary->secondary", cooldowns=cooldowns)

    with pytest.raises(ProviderRateLimitError) as exc_info:
        fallback.generate("query")

    assert exc_info.value.is_account_limit is True
    assert p2.call_count == 0  # Failover stopped immediately

    entry = cooldowns.check("primary")
    assert entry is not None
    assert entry.reason == "QUOTA_EXHAUSTED"
    assert entry.until == clock.current + ACCOUNT_LIMIT_COOLDOWN_S


def test_primary_rate_limit_does_not_retry_after_1s():
    """Primary provider encountering 429 is called exactly once (no 1s transient retry)."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("primary")
    p2 = DummyProvider("secondary")

    p1_calls = 0

    def p1_fail(prompt):
        nonlocal p1_calls
        p1_calls += 1
        raise ProviderRateLimitError("Rate limited 429")

    p1.generate = p1_fail

    fallback = FallbackProvider([p1, p2], "primary->secondary", cooldowns=cooldowns)

    with patch("time.sleep") as mock_sleep:
        res = fallback.generate("hello")
        assert res == "secondary: hello"
        assert p1_calls == 1
        mock_sleep.assert_not_called()


def test_primary_transient_error_retries_once():
    """Primary provider encountering plain ProviderUnavailableError still retries once after 1s."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("primary")

    p1_calls = 0

    def p1_behavior(prompt):
        nonlocal p1_calls
        p1_calls += 1
        if p1_calls == 1:
            raise ProviderUnavailableError("Temporary network glitch")
        return "primary: retry success"

    p1.generate = p1_behavior

    fallback = FallbackProvider([p1], "primary", cooldowns=cooldowns)

    with patch("time.sleep") as mock_sleep:
        res = fallback.generate("test")
        assert res == "primary: retry success"
        assert p1_calls == 2
        mock_sleep.assert_called_once_with(1.0)


def test_all_providers_cooling_down_raises_last_error():
    """When all providers are cooling down, raises the stored error with zero provider calls."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("p1")
    p2 = DummyProvider("p2")

    p1.generate = MagicMock(side_effect=ProviderAuthError("Key invalid", reason=AUTH_INVALID))
    p2.generate = MagicMock(side_effect=ProviderRateLimitError("Rate limited"))

    fallback = FallbackProvider([p1, p2], "p1->p2", cooldowns=cooldowns)

    # First request: trips both into cooldown
    with pytest.raises(ProviderRateLimitError):
        fallback.generate("req 1")

    p1.generate.reset_mock()
    p2.generate.reset_mock()

    # Second request: both skipped in cooldown
    with pytest.raises(ProviderRateLimitError):
        fallback.generate("req 2")

    p1.generate.assert_not_called()
    p2.generate.assert_not_called()


def test_stream_and_tools_cooldown_behavior():
    """Streaming and generate_with_tools observe and update cooldown state identically."""
    clock = FakeClock()
    cooldowns = ProviderCooldowns(clock=clock)

    p1 = DummyProvider("p1")
    p2 = DummyProvider("p2")

    def p1_stream(prompt):
        raise ProviderAuthError("Stream auth failed", reason=AUTH_FORBIDDEN)

    p1.stream = p1_stream

    fallback = FallbackProvider([p1, p2], "p1->p2", cooldowns=cooldowns)

    # Stream fails p1, succeeds on p2
    chunks = list(fallback.stream("prompt 1"))
    assert "".join(chunks) == "p2: prompt 1"

    # p1 is now in cooldown
    entry = cooldowns.check("p1")
    assert entry is not None
    assert entry.reason == AUTH_FORBIDDEN

    # Now call generate_with_tools: p1 is skipped from cooldown
    def p1_tools(sys, msg, tools):
        pytest.fail("p1 should have been skipped in cooldown!")

    p1.generate_with_tools = p1_tools
    res = fallback.generate_with_tools("sys", [], [])
    assert res == {"content": "p2 tools"}
