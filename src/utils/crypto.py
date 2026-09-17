"""
Cryptographic utility module for encrypting and decrypting sensitive data (passwords).
Uses Fernet (AES-128-CBC + HMAC-SHA256 authenticated encryption) with a deterministic key.
"""
import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from config.settings import settings

logger = logging.getLogger(__name__)


class CryptoUtil:
    """Utility for reversible encryption/decryption of sensitive data (passwords)."""

    _fernet: Optional[Fernet] = None

    @classmethod
    def _get_fernet(cls) -> Fernet:
        """Get or initialize the Fernet instance."""
        if cls._fernet is None:
            raw_key = getattr(settings, 'ENCRYPTION_KEY', None)
            if not raw_key:
                # Derive stable 32-byte key from TELEGRAM_BOT_TOKEN
                secret = settings.TELEGRAM_BOT_TOKEN or "agent_conductor_default_master_key_2026"
                raw_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
            elif isinstance(raw_key, str):
                raw_key = raw_key.encode()
            cls._fernet = Fernet(raw_key)
        return cls._fernet

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        """
        Encrypt plaintext string to base64 ciphertext.

        Args:
            plaintext: Plain text password or sensitive data

        Returns:
            Encrypted base64 ciphertext string
        """
        if not plaintext:
            return ""
        try:
            return cls._get_fernet().encrypt(plaintext.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return plaintext

    @classmethod
    def decrypt(cls, ciphertext: str) -> str:
        """
        Decrypt base64 ciphertext string back to plaintext.
        Maintains full backward compatibility with unencrypted legacy strings.

        Args:
            ciphertext: Encrypted Fernet token or legacy plaintext

        Returns:
            Decrypted plaintext string
        """
        if not ciphertext:
            return ""
        # Fernet tokens start with 'gAAAAA'
        if not ciphertext.startswith("gAAAAA"):
            return ciphertext
        try:
            return cls._get_fernet().decrypt(ciphertext.encode()).decode()
        except (InvalidToken, Exception) as e:
            logger.warning(f"Decryption failed, returning as-is: {e}")
            return ciphertext
