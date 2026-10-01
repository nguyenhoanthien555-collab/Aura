import pytest
from memory.sanitizer import SensitiveDataSanitizer, _luhn_check


def test_luhn_check():
    # Valid Visa test number (4532 0151 1283 0366)
    assert _luhn_check("4532015112830366") is True
    # Invalid card number
    assert _luhn_check("4532015112830367") is False
    # Short string
    assert _luhn_check("12345") is False


def test_sensitive_api_keys():
    # Google API Key
    google_key = "AIzaSyD-7389274928374923749238472938472"
    assert SensitiveDataSanitizer.is_sensitive(f"Here is my key: {google_key}") is True

    redacted = SensitiveDataSanitizer.redact(f"Key={google_key}")
    assert google_key not in redacted
    assert "[REDACTED_CREDENTIAL]" in redacted

    # OpenAI sk- key
    sk_key = "sk-proj-1234567890abcdef1234567890abcdef"
    assert SensitiveDataSanitizer.is_sensitive(f"token: {sk_key}") is True


def test_sensitive_passwords():
    assert SensitiveDataSanitizer.is_sensitive("mật khẩu là: 12345678") is True
    assert SensitiveDataSanitizer.is_sensitive("password: secretpassword123") is True
    assert SensitiveDataSanitizer.is_sensitive("Mã PIN: 9988") is True

    redacted = SensitiveDataSanitizer.redact("Mật khẩu là: SuperSecret99!")
    assert "SuperSecret99!" not in redacted


def test_validate_for_storage():
    ok, err = SensitiveDataSanitizer.validate_for_storage("favorite_fruit", "Xoài Cát Hòa Lộc")
    assert ok is True
    assert err is None

    ok, err = SensitiveDataSanitizer.validate_for_storage("api_key", "AIzaSyD-7389274928374923749238472938472")
    assert ok is False
    assert err is not None
