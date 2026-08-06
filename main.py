"""
Aura Entry Point
"""

from dotenv import load_dotenv

from core.app import Aura


def main():

    # Load .env
    load_dotenv()

    aura = Aura()
    aura.start()


if __name__ == "__main__":
    main()