"""Tests for API key encryption."""

import pytest
from app.services.encryption import encrypt, decrypt


class TestEncryption:
    def test_roundtrip(self):
        original = "sk-1234567890abcdef"
        encrypted = encrypt(original)
        assert encrypted != original
        assert decrypt(encrypted) == original

    def test_different_ciphertexts(self):
        """Each encryption should produce a different ciphertext (Fernet uses random IV)."""
        original = "sk-test-key"
        c1 = encrypt(original)
        c2 = encrypt(original)
        assert c1 != c2  # different IVs
        assert decrypt(c1) == decrypt(c2) == original
