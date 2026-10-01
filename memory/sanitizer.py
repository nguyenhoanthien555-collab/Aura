"""
Sensitive Data Sanitizer for AURA Memory.

Guarantees privacy-by-default by detecting and sanitizing credentials,
API tokens, payment cards, and passwords before persistence into long-term memory.
"""

import re
from typing import Optional


# Patterns for API Keys and Secrets
_SECRET_PATTERNS = [
    # Google API Key
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    # OpenAI / Anthropic / generic sk- keys
    re.compile(r"sk-[a-zA-Z0-9\-_]{20,}"),
    re.compile(r"ant-[a-zA-Z0-9\-_]{20,}"),
    # GitHub Tokens
    re.compile(r"gh[pousr]_[a-zA-Z0-9]{36,255}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{82}"),
    # Generic Bearer Token
    re.compile(r"(?:bearer|token)\s+[a-zA-Z0-9\-._~+/]+=*", re.IGNORECASE),
    # Private Key blocks
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
]

# Patterns for Passwords, PINs, OTPs in text
_PASSWORD_PATTERNS = [
    re.compile(r"(?:password|mật khẩu|mat khau|secret|mã pin|cvv|otp)\s*(?:là|is)?\s*[:=]?\s*([^\s,;]{4,})", re.IGNORECASE),
]

# Credit Card Pattern (13-19 digits, possibly with dashes or spaces)
_CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _luhn_check(number_str: str) -> bool:
    """Validate credit card number using Luhn algorithm."""
    digits = [int(c) for c in number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for idx, d in enumerate(reverse_digits):
        if idx % 2 == 1:
            doubled = d * 2
            checksum += (doubled - 9) if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


class SensitiveDataSanitizer:
    """
    Sanitizes user input and memory values to protect private credentials.
    """

    @classmethod
    def is_sensitive(cls, text: str) -> bool:
        """Check if string contains sensitive credentials or payment cards."""
        if not text or not isinstance(text, str):
            return False

        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                return True

        for pattern in _PASSWORD_PATTERNS:
            if pattern.search(text):
                return True

        for match in _CARD_PATTERN.finditer(text):
            candidate = match.group(0).replace(" ", "").replace("-", "")
            if _luhn_check(candidate):
                return True

        return False

    @classmethod
    def redact(cls, text: str) -> str:
        """
        Redact sensitive tokens with placeholders.
        """
        if not text or not isinstance(text, str):
            return text

        result = text

        for pattern in _SECRET_PATTERNS:
            result = pattern.sub("[REDACTED_CREDENTIAL]", result)

        for pattern in _PASSWORD_PATTERNS:
            def _replace_pw(m):
                full = m.group(0)
                val = m.group(1)
                return full.replace(val, "[REDACTED_SECRET]")
            result = pattern.sub(_replace_pw, result)

        for match in _CARD_PATTERN.finditer(result):
            matched_str = match.group(0)
            candidate = matched_str.replace(" ", "").replace("-", "")
            if _luhn_check(candidate):
                result = result.replace(matched_str, "[REDACTED_CARD]")

        return result

    @classmethod
    def validate_for_storage(cls, key: str, value: str) -> tuple[bool, Optional[str]]:
        """
        Returns (True, None) if safe, or (False, reason) if rejected due to sensitive data.
        """
        if cls.is_sensitive(key):
            return False, "Key contains sensitive keyword or credential"

        if cls.is_sensitive(value):
            return False, "Value contains sensitive credential, password, or payment card"

        return True, None
