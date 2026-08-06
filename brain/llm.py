"""
LLM interface.

Kept as the historical import path. The canonical definition now lives
in brain.ports so that every interface the brain depends on sits in one
place.
"""

from brain.ports import LLM

__all__ = ["LLM"]
