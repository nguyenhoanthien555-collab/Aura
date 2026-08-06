"""
Voice.

Two independent halves:

    microphone -> STT provider -> text  -> (caller feeds the brain)
    Response   -> TTS engine   -> audio

The brain imports neither. Voice reaches the brain by handing it text,
and hears back from the brain by subscribing to ResponseEvent on the
event bus. Nothing in this package is imported by brain/.
"""
