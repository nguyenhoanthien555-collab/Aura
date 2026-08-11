"""
List available Gemini models.

Run directly: python scripts/manual_list_models.py
"""

import os

from dotenv import load_dotenv
from google import genai


def main():

    load_dotenv()

    client = genai.Client(
        api_key=os.getenv("GEMINI_API_KEY")
    )

    print("===== AVAILABLE MODELS =====\n")

    for model in client.models.list():
        print(model.name)


if __name__ == "__main__":
    main()
