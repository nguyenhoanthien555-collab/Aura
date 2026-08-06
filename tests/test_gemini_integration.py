"""
Gemini provider integration test.

Opt-in only: this test makes real network calls and costs quota, so it
is skipped unless BOTH are true:

    AURA_RUN_INTEGRATION=1   is set
    GEMINI_API_KEY           is available

Run it with:
    AURA_RUN_INTEGRATION=1 pytest tests/test_gemini_integration.py -v
"""

import os

import pytest
from dotenv import load_dotenv

from brain.router import BrainRouter


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gemini_router():

    if os.getenv("AURA_RUN_INTEGRATION") != "1":
        pytest.skip("set AURA_RUN_INTEGRATION=1 to run integration tests")

    load_dotenv()

    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not found")

    return BrainRouter(provider_name="gemini")


def test_gemini_provider_returns_text(gemini_router):
    """The configured Gemini model answers, and honours the -> str contract."""

    response = gemini_router.generate(
        "Say hello in one short sentence."
    )

    assert isinstance(response, str)
    assert response.strip()
