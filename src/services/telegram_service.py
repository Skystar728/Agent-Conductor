"""Telegram messaging service."""
import requests
from typing import Optional

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class TelegramService:
    """Service for sending messages via Telegram Bot API."""

    def __init__(self, bot_token: Optional[str] = None):
        """
        Initialize Telegram service.

        Args:
            bot_token: Telegram bot token (defaults to settings.TELEGRAM_BOT_TOKEN)
        """
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.base_url = settings.TELEGRAM_API_BASE_URL.format(token=self.bot_token)
        self.session = requests.session()

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
        """
        Send a text message to a Telegram chat with optional inline keyboard buttons.

        Args:
            chat_id: Telegram chat ID
            text: Message text to send
            reply_markup: Optional dictionary for inline keyboards or custom markup

        Returns:
            True if message was sent successfully, False otherwise
        """
        try:
            url = f"{self.base_url}/sendMessage"
            cleaned_text = str(text or "").replace("**", "")
            payload = {
                "chat_id": chat_id,
                "text": cleaned_text
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
                response = self.session.post(url, json=payload, timeout=10)
            else:
                response = self.session.post(url, json=payload, timeout=10)

            response.raise_for_status()
            logger.info(f"Message sent to chat_id={chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to chat_id={chat_id}: {e}")
            return False

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False) -> bool:
        """
        Acknowledge a callback query from an inline keyboard button click.

        Args:
            callback_query_id: Unique identifier for the query to be answered
            text: Notification text shown to user as toast (optional)
            show_alert: If True, an alert modal will be shown instead of a toast

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/answerCallbackQuery"
            payload = {"callback_query_id": callback_query_id}
            if text:
                payload["text"] = text
            if show_alert:
                payload["show_alert"] = show_alert
            response = self.session.post(url, json=payload, timeout=5)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to answer callback query {callback_query_id}: {e}")
            return False

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """
        Delete a message from Telegram chat history.

        Args:
            chat_id: Telegram chat ID
            message_id: ID of the message to delete

        Returns:
            True if message was deleted successfully, False otherwise
        """
        try:
            url = f"{self.base_url}/deleteMessage"
            payload = {"chat_id": chat_id, "message_id": message_id}
            response = self.session.post(url, json=payload, timeout=5)
            response.raise_for_status()
            logger.debug(f"Deleted message {message_id} in chat_id={chat_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete message {message_id} in chat_id={chat_id}: {e}")
            return False

    def setup_bot_commands(self) -> bool:
        """
        Register default bot commands with Telegram so the Menu button appears.
        """
        commands = [
            {"command": "start", "description": "🚀 열차 여정 시작"},
            {"command": "status", "description": "📊 현재 여정 상태 확인"},
            {"command": "cancel", "description": "❌ 진행 중인 여정 취소"},
            {"command": "help", "description": "💡 사용 방법 및 명령어 안내"},
            {"command": "edit", "description": "✏️ 여정 조건 수정"},
            {"command": "seat", "description": "🪑 좌석 배치(연속/랜덤) 변경"}
        ]
        try:
            url = f"{self.base_url}/setMyCommands"
            res = self.session.post(url, json={"commands": commands}, timeout=10)
            res.raise_for_status()
            logger.info("Successfully registered bot commands to Telegram")

            # Explicitly set the chat menu button to show commands
            try:
                menu_btn_url = f"{self.base_url}/setChatMenuButton"
                self.session.post(menu_btn_url, json={"menu_button": {"type": "commands"}}, timeout=5)
            except Exception as e:
                logger.debug(f"Optional setChatMenuButton error (ignored): {e}")

            return True
        except Exception as e:
            logger.error(f"Failed to register bot commands: {e}")
            return False

    @staticmethod
    def build_inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
        """
        Helper to construct an inline_keyboard reply_markup.

        Args:
            rows: List of button rows, where each button is (display_text, callback_data)

        Returns:
            Dictionary suitable for reply_markup parameter
        """
        keyboard = []
        for row in rows:
            keyboard_row = []
            for text, data in row:
                keyboard_row.append({"text": text, "callback_data": str(data)})
            keyboard.append(keyboard_row)
        return {"inline_keyboard": keyboard}

    def send_to_multiple(self, chat_ids: list[int], text: str, reply_markup: Optional[dict] = None) -> int:
        """
        Send a message to multiple chats.

        Args:
            chat_ids: List of Telegram chat IDs
            text: Message text to send
            reply_markup: Optional reply markup

        Returns:
            Number of successful sends
        """
        success_count = 0
        for chat_id in chat_ids:
            if self.send_message(chat_id, text, reply_markup=reply_markup):
                success_count += 1
        return success_count


# MessageTemplates class has been deprecated and replaced by Messages class
# Import Messages from telegramBot.messages for all message templates
from telegramBot.messages import Messages as MessageTemplates

# For backward compatibility, create an alias
# This allows existing code to continue using MessageTemplates.method_name()
# while actually calling the centralized Messages class
