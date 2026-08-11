"""
Manual memory test.

Run directly: python scripts/manual_memory_test.py
"""

from memory.manager import MemoryManager
from brain.adapters import records_to_messages


def main():

    memory = MemoryManager()

    print("\n===== Saving messages =====\n")

    memory.save("user", "First test message")
    memory.save("assistant", "First reply")
    memory.save("user", "Second question")
    memory.save("assistant", "Second reply")

    print("Saved 4 messages.")

    print("\n===== Retrieving recent (newest first) =====\n")

    records = memory.get_recent(10)

    for record in records:
        print(f"{record.role}: {record.content}")

    print("\n===== Converting to pipeline Messages (oldest first) =====\n")

    messages = records_to_messages(records)
    messages.reverse()

    for msg in messages:
        print(f"{msg.role}: {msg.content}")

    print()


if __name__ == "__main__":
    main()
