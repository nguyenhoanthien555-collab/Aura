"""
Clear Aura memory database.

Run directly: python scripts/manual_clear_memory.py
"""

from memory.manager import MemoryManager


def main():

    memory = MemoryManager()

    memory.clear()

    print("Memory cleared successfully.")


if __name__ == "__main__":
    main()
