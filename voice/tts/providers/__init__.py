"""
Text to speech providers.

Bundled:
    mock    always works, records what was said, used by every test
    sapi    Windows System.Speech, no install required
    pyttsx  cross platform, needs the optional `pyttsx3` package

Planned drop in points (edge-tts, ElevenLabs, Kokoro) only need a class
with `speak(text)` plus one line in voice/factory.py.
"""
