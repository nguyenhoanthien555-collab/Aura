"""
Speech to text.
"""

from voice.stt.provider import AudioChunk, SpeechToTextProvider
from voice.stt.microphone import Microphone, MockMicrophone
from voice.stt.engine import SpeechToTextEngine

__all__ = [
    "AudioChunk",
    "SpeechToTextProvider",
    "Microphone",
    "MockMicrophone",
    "SpeechToTextEngine",
]
