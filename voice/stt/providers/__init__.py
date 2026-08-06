"""
Speech to text providers.

Bundled: mock (always works), whisper (optional, local).

To add one, implement `transcribe(audio) -> str` and register the name in
voice/factory.py. Nothing else needs to change.
"""
