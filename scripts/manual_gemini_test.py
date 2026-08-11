"""
Quick smoke test for the Gemini provider.

Run directly: python scripts/manual_gemini_test.py
"""

from brain.router import BrainRouter


def main():

    router = BrainRouter(provider_name="gemini")

    print("\n===== Calling Gemini =====\n")

    response = router.generate(
        "Say hello in one short sentence."
    )

    print("\n===== RESPONSE =====\n")
    print(response)
    print()


if __name__ == "__main__":
    main()
