"""
Prompt preview.

Renders a full prompt exactly as the pipeline would build it, so you can
eyeball the SYSTEM / PERSONALITY / CONTEXT / MEMORY / HISTORY / USER
sections.

Run directly: python scripts/manual_prompt_test.py
"""

from brain.adapters import records_to_messages
from brain.message import Message
from brain.prompt_builder import PromptBuilder
from memory.manager import MemoryManager


def main():

    memory = MemoryManager()
    builder = PromptBuilder()

    # Storage returns newest-first; the pipeline wants oldest-first.
    records = memory.get_recent(5)
    history = records_to_messages(records)
    history.reverse()

    prompt = builder.build(
        history=history,
        user_message=Message(
            role="user",
            content="Hello Aura!",
        ),
        contexts=[],
    )

    print("\n" + "=" * 60)
    print("PROMPT PREVIEW")
    print("=" * 60)
    print(prompt)
    print("=" * 60)


if __name__ == "__main__":
    main()
