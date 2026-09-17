"""
Application configuration management.

This module centralizes all configuration variables from environment
and provides type-safe access to settings throughout the application.
"""
import os
from typing import Optional


class SettingsValidationError(ValueError):
    pass


class Settings:
    """Application settings loaded from environment variables."""

    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = os.environ.get('BOTTOKEN', '')
    TELEGRAM_API_BASE_URL: str = "https://api.telegram.org/bot{token}"

    # Train Configuration
    TRAIN_ADMIN_USER_ID: Optional[str] = os.environ.get('TRAIN_USERID') or os.environ.get('USERID')
    TRAIN_ADMIN_PASSWORD: Optional[str] = os.environ.get('TRAIN_USERPW') or os.environ.get('USERPW')
    TRAIN_SEARCH_INTERVAL: float = float(os.environ.get('TRAIN_SEARCH_INTERVAL') or os.environ.get('SEARCH_INTERVAL', '2.5'))  # seconds
    TRAIN_SEARCH_MIN_INTERVAL: float = float(os.environ.get('TRAIN_SEARCH_MIN_INTERVAL') or os.environ.get('KORAIL_SEARCH_MIN_INTERVAL', '3.0'))
    TRAIN_SEARCH_MAX_INTERVAL: float = float(os.environ.get('TRAIN_SEARCH_MAX_INTERVAL') or os.environ.get('KORAIL_SEARCH_MAX_INTERVAL', '5.5'))
    TRAIN_STATION_LIST_URL: str = "https://www.korail.com/ticket/train/stationGuide/station"
    TRAIN_PAYMENT_URL: str = "https://www.korail.com/ticket/myticket/list"

    # Backward compatibility aliases
    KORAIL_ADMIN_USER_ID = TRAIN_ADMIN_USER_ID
    KORAIL_ADMIN_PASSWORD = TRAIN_ADMIN_PASSWORD
    KORAIL_SEARCH_INTERVAL = TRAIN_SEARCH_INTERVAL
    KORAIL_SEARCH_MIN_INTERVAL = TRAIN_SEARCH_MIN_INTERVAL
    KORAIL_SEARCH_MAX_INTERVAL = TRAIN_SEARCH_MAX_INTERVAL
    KORAIL_STATION_LIST_URL = TRAIN_STATION_LIST_URL
    KORAIL_PAYMENT_URL = TRAIN_PAYMENT_URL

    # AI / LLM Configuration (OpenAI SDK Compatible)
    # Works seamlessly with OpenAI directly, local OmniRoute, Ollama, Groq, vLLM, etc.
    AI_GATEWAY: str = os.environ.get('AI_GATEWAY', 'none').strip().lower()

    LLM_API_KEY: str = (
        os.environ.get('OPENAI_API_KEY') or
        os.environ.get('LLM_API_KEY') or
        os.environ.get('OMNIROUTE_API_KEY') or
        os.environ.get('NVIDIA_API_KEY') or
        ('local-internal-key' if AI_GATEWAY == 'omniroute' else '')
    )
    LLM_BASE_URL: str = (
        os.environ.get('OPENAI_BASE_URL') or
        os.environ.get('LLM_BASE_URL') or
        os.environ.get('OMNIROUTE_BASE_URL') or
        os.environ.get('NVIDIA_BASE_URL') or
        ('http://omniroute:20128/v1' if AI_GATEWAY == 'omniroute' else 'https://api.openai.com/v1')
    )
    LLM_MODEL: str = (
        os.environ.get('OPENAI_MODEL') or
        os.environ.get('LLM_MODEL') or
        os.environ.get('OMNIROUTE_MODEL') or
        os.environ.get('NVIDIA_MODEL') or
        ('cl/google/gemma-4-31b-it:free' if AI_GATEWAY == 'omniroute' else 'gpt-4o-mini')
    )

    # Aliases for backwards compatibility
    OPENAI_API_KEY: str = LLM_API_KEY
    OPENAI_BASE_URL: str = LLM_BASE_URL
    OPENAI_MODEL: str = LLM_MODEL
    NVIDIA_API_KEY: str = LLM_API_KEY
    NVIDIA_BASE_URL: str = LLM_BASE_URL
    NVIDIA_MODEL: str = LLM_MODEL

    # User Access Control
    ALLOW_LIST: list[str] = os.environ.get('ALLOW_LIST', '').split(',') if os.environ.get('ALLOW_LIST') else []
    MASTER_USER_LIST: list[str] = (
        [x.strip() for x in os.environ.get('MASTER_USER_LIST', os.environ.get('ADMIN_CHAT_IDS', '')).split(',') if x.strip()]
    )

    # Payment Reminder Configuration
    PAYMENT_TIMEOUT_MINUTES: int = int(os.environ.get('PAYMENT_TIMEOUT_MINUTES', '10'))
    PAYMENT_REMINDER_INTERVAL_SECONDS: int = int(os.environ.get('PAYMENT_REMINDER_INTERVAL', '30'))

    # Flask Configuration
    FLASK_HOST: str = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT: int = int(os.environ.get('FLASK_PORT', '8080'))
    FLASK_DEBUG: bool = os.environ.get('FLASK_DEBUG', 'True').lower() in ('true', '1', 'yes')

    # Application Callback URLs (internal)
    CALLBACK_BASE_URL: str = f"http://127.0.0.1:{FLASK_PORT}"

    # Logging Configuration
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FORMAT: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Master Admin Auto-Login Trigger Keyword
    ADMIN_MAGIC_STRING: str = os.environ.get('ADMIN_MAGIC_STRING', "마스터로그인")

    # Admin Command Authentication
    # Uses USERPW environment variable (same as Korail admin password)
    ADMIN_PASSWORD: Optional[str] = os.environ.get('USERPW')

    # Process Management
    RECURSION_LIMIT: int = 10**7

    # Redis Configuration
    REDIS_HOST: str = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT: int = int(os.environ.get('REDIS_PORT', '6379'))
    REDIS_DB: int = int(os.environ.get('REDIS_DB', '0'))
    REDIS_PASSWORD: Optional[str] = os.environ.get('REDIS_PASSWORD')
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_MAX_CONNECTIONS: int = 50

    # WAF / Bot Mitigation Circuit Breaker
    WAF_COOLDOWN_SECONDS: int = int(os.environ.get('WAF_COOLDOWN_SECONDS', '180'))  # 3 minutes cooldown
    WAF_MAX_CONSECUTIVE_BLOCKS: int = int(os.environ.get('WAF_MAX_CONSECUTIVE_BLOCKS', '2'))
    RATE_LIMIT_COOLDOWN_SECONDS: int = int(os.environ.get('RATE_LIMIT_COOLDOWN_SECONDS', '10'))  # 10s cooldown for 429/temporary traffic
    MAINTENANCE_CHECK_MIN_INTERVAL: float = float(os.environ.get('MAINTENANCE_CHECK_MIN_INTERVAL', '180.0'))  # 3 minutes
    MAINTENANCE_CHECK_MAX_INTERVAL: float = float(os.environ.get('MAINTENANCE_CHECK_MAX_INTERVAL', '300.0'))  # 5 minutes

    @classmethod
    def validate(cls) -> None:
        """Validate required settings."""
        if not cls.TELEGRAM_BOT_TOKEN:
            raise SettingsValidationError("BOTTOKEN environment variable is required")
        if not 0 < cls.TRAIN_SEARCH_MIN_INTERVAL <= cls.TRAIN_SEARCH_MAX_INTERVAL:
            raise SettingsValidationError(
                "TRAIN_SEARCH_MIN_INTERVAL must be positive and no greater than "
                "TRAIN_SEARCH_MAX_INTERVAL"
            )

    def _read_env_list_var(self, primary_key: str, fallback_key: Optional[str] = None) -> list[str]:
        """Read a comma-separated list variable dynamically from .env files or environment."""
        env_paths = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'),
            '/app/.env',
            '.env'
        ]
        prefix1 = f"{primary_key}="
        prefix2 = f"{fallback_key}=" if fallback_key else None

        for env_path in env_paths:
            if os.path.exists(env_path):
                try:
                    with open(env_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith(prefix1) or (prefix2 and line.startswith(prefix2)):
                                val = line.split('=', 1)[1].strip().strip('"').strip("'")
                                return [x.strip() for x in val.split(',') if x.strip()]
                except Exception:
                    pass

        raw = os.environ.get(primary_key, os.environ.get(fallback_key or '', ''))
        return [x.strip() for x in raw.split(',') if x.strip()]

    def get_allow_list(self) -> list[str]:
        """Get allow list dynamically, supporting live updates from .env or env var."""
        return self._read_env_list_var('ALLOW_LIST')

    def is_user_allowed(self, phone_number: str) -> bool:
        """Check if user phone number is in allow list."""
        allow_list = self.get_allow_list()
        if not allow_list:
            return True  # No restriction if ALLOW_LIST is empty
        return phone_number in allow_list

    def get_master_user_list(self) -> list[str]:
        """Get master login allowed user list dynamically, supporting live updates from .env or env var."""
        return self._read_env_list_var('MASTER_USER_LIST', 'ADMIN_CHAT_IDS')

    def is_master_user_allowed(self, chat_id: int, user_identifier: Optional[str] = None) -> bool:
        """
        Check if chat_id or associated user identifier is authorized for master auto-login.
        
        Returns False if master user list is empty or identifier does not match.
        """
        allowed = self.get_master_user_list()
        if not allowed:
            return False  # Strict security: require explicit authorization

        cid_str = str(chat_id)
        if cid_str in allowed:
            return True
        if user_identifier and str(user_identifier) in allowed:
            return True
        return False


# Singleton instance
settings = Settings()
