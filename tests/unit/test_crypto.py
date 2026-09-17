"""Unit tests for CryptoUtil (Fernet AES encryption/decryption)."""
import unittest
from utils.crypto import CryptoUtil


class TestCryptoUtil(unittest.TestCase):
    """Test suite for CryptoUtil."""

    def test_encrypt_and_decrypt(self):
        """Test encryption and decryption of plaintext password."""
        password = "mySecretPassword123!@#"
        encrypted = CryptoUtil.encrypt(password)

        self.assertNotEqual(password, encrypted)
        self.assertTrue(encrypted.startswith("gAAAAA"))

        decrypted = CryptoUtil.decrypt(encrypted)
        self.assertEqual(password, decrypted)

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        self.assertEqual(CryptoUtil.encrypt(""), "")
        self.assertEqual(CryptoUtil.decrypt(""), "")

    def test_legacy_plaintext_compatibility(self):
        """Test that unencrypted legacy plaintext is returned as-is."""
        legacy_pw = "plain_unencrypted_pw"
        self.assertEqual(CryptoUtil.decrypt(legacy_pw), legacy_pw)

    def test_corrupted_token_fallback(self):
        """Test that corrupted token falls back safely to original text."""
        corrupted = "gAAAAABcorruptedTokenHere12345"
        self.assertEqual(CryptoUtil.decrypt(corrupted), corrupted)


if __name__ == '__main__':
    unittest.main()
