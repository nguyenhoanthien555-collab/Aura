"""
Check that the .env file is loaded.

Run directly: python tests/manual_env_check.py
"""

import os

from dotenv import load_dotenv


def main():

    load_dotenv()

    key = os.getenv("GEMINI_API_KEY")

    if not key:
        print("GEMINI_API_KEY: NOT FOUND")
        return

    # Never print the key itself.
    print(f"GEMINI_API_KEY: found ({len(key)} chars)")


if __name__ == "__main__":
    main()
