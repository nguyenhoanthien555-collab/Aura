"""
Builtin memory management tools.

Enables conversational storage and deletion of durable user facts.
Protected by SensitiveDataSanitizer to guarantee privacy.
"""

from typing import Optional

from core.logger import logger
from memory.profile import ProfileStore, normalise_key
from memory.sanitizer import SensitiveDataSanitizer
from tools.base import Parameter, SideEffect, Tool, ToolResult, ToolRisk, fail, ok


class RememberFactTool(Tool):
    """
    Store a personal fact, preference, or trait about the user into long-term memory.
    """

    name = "remember_fact"
    description = (
        "Store a personal fact, preference, or trait about the user into long-term memory. "
        "Use this when the user tells you their name, habits, preferences, favorite things, or background."
    )
    risk = ToolRisk.SAFE
    side_effect = SideEffect.IDEMPOTENT
    capability = "memory.remember"

    parameters = (
        Parameter(
            name="key",
            description="A short unique slug for the fact (e.g. 'favorite_drink', 'occupation', 'hometown')",
            type="string",
            required=True,
        ),
        Parameter(
            name="value",
            description="The value or statement to remember (e.g. 'Bac Xiu Coffee', 'Software Engineer', 'Da Nang')",
            type="string",
            required=True,
        ),
        Parameter(
            name="category",
            description="Grouping category: 'profile', 'preference', 'work', 'health', or 'general'",
            type="string",
            required=False,
        ),
    )

    def __init__(self, profile_store: Optional[ProfileStore] = None):
        self._profile_store = profile_store if profile_store is not None else ProfileStore()

    def execute(self, key: str, value: str, category: str = "profile") -> ToolResult:
        if not key or not isinstance(key, str):
            return fail("A valid key string is required", tool=self.name)

        if not value or not isinstance(value, str):
            return fail("A non-empty value string is required", tool=self.name)

        clean_key = normalise_key(key)
        clean_val = value.strip()
        clean_cat = (category or "profile").strip().lower()

        # Privacy gate
        is_safe, reason = SensitiveDataSanitizer.validate_for_storage(clean_key, clean_val)
        if not is_safe:
            logger.warning("RememberFactTool rejected sensitive input: %s", reason)
            return fail(
                f"Privacy refusal: {reason}. Credentials, passwords, and card numbers are never stored in memory.",
                tool=self.name,
            )

        try:
            fact = self._profile_store.remember(
                key=clean_key,
                value=clean_val,
                category=clean_cat,
                source="user",
            )
            if fact is None:
                return fail(f"Failed to record fact '{clean_key}'", tool=self.name)

            return ok(f"Saved to memory: '{clean_key}' = '{clean_val}' ({clean_cat})", tool=self.name)
        except Exception as e:
            logger.error("RememberFactTool error: %s", e)
            return fail(f"Storage error: {e}", tool=self.name)


class ForgetFactTool(Tool):
    """
    Remove a stored personal fact or preference about the user from long-term memory.
    """

    name = "forget_fact"
    description = (
        "Remove a stored fact or preference about the user from long-term memory. "
        "Use this when the user asks you to forget or erase something you previously remembered."
    )
    risk = ToolRisk.SAFE
    side_effect = SideEffect.IDEMPOTENT
    capability = "memory.forget"

    parameters = (
        Parameter(
            name="key",
            description="The key or slug of the fact to forget (e.g. 'favorite_drink')",
            type="string",
            required=True,
        ),
    )

    def __init__(self, profile_store: Optional[ProfileStore] = None):
        self._profile_store = profile_store if profile_store is not None else ProfileStore()

    def execute(self, key: str) -> ToolResult:
        if not key or not isinstance(key, str):
            return fail("A valid key string is required", tool=self.name)

        clean_key = normalise_key(key)

        try:
            forgotten = self._profile_store.forget(clean_key)
            if not forgotten:
                return ok(f"Fact '{clean_key}' was not present in memory.", tool=self.name)

            return ok(f"Successfully erased '{clean_key}' from memory.", tool=self.name)
        except Exception as e:
            logger.error("ForgetFactTool error: %s", e)
            return fail(f"Deletion error: {e}", tool=self.name)


__all__ = [
    "RememberFactTool",
    "ForgetFactTool",
]
