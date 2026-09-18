import pytest
from unittest.mock import MagicMock, patch
import requests

from models.user import UserProgress, UserSession, UserCredentials
from services.korail_service import KorailService
from services.telegram_service import TelegramService
from handlers.command_handler import CommandHandler
from services.payment_reminder_service import PaymentReminderService


class TestSecurityAndEncoding:
    def test_fix_korail_response_encoding(self):
        """Test response hook fixes ISO-8859-1 / None / latin-1 to utf-8."""
        mock_resp1 = MagicMock(spec=requests.Response)
        mock_resp1.encoding = "ISO-8859-1"
        KorailService._fix_korail_response_encoding(mock_resp1)
        assert mock_resp1.encoding == "utf-8"

        mock_resp2 = MagicMock(spec=requests.Response)
        mock_resp2.encoding = None
        KorailService._fix_korail_response_encoding(mock_resp2)
        assert mock_resp2.encoding == "utf-8"

        mock_resp3 = MagicMock(spec=requests.Response)
        mock_resp3.encoding = "latin-1"
        KorailService._fix_korail_response_encoding(mock_resp3)
        assert mock_resp3.encoding == "utf-8"

        mock_resp4 = MagicMock(spec=requests.Response)
        mock_resp4.encoding = "euc-kr"
        KorailService._fix_korail_response_encoding(mock_resp4)
        assert mock_resp4.encoding == "euc-kr"

    def test_status_command_registered_in_telegram_menu(self):
        """Verify /status is present in Telegram commands list."""
        service = TelegramService("dummy_token")
        with patch.object(service.session, "post") as mock_post:
            mock_res = MagicMock()
            mock_res.raise_for_status.return_value = None
            mock_post.return_value = mock_res

            service.setup_bot_commands()
            assert mock_post.call_count == 2
            set_commands_payload = mock_post.call_args_list[0][1]["json"]
            commands = {c["command"]: c["description"] for c in set_commands_payload["commands"]}
            assert "status" in commands
            assert "현재 여정 상태" in commands["status"]

    def test_handle_start_always_wipes_credentials_and_asks_input(self):
        """Verify /start resets credentials and prompts for phone/ID."""
        mock_storage = MagicMock()
        mock_telegram = MagicMock()
        mock_reservation = MagicMock()
        mock_reminder = MagicMock()

        handler = CommandHandler(mock_storage, mock_telegram, mock_reservation, mock_reminder)

        # Even if session has existing credentials
        existing_session = UserSession(chat_id=12345)
        existing_session.credentials = UserCredentials(train_id="01012345678", train_pw="secret")
        mock_storage.get_user_session.return_value = existing_session

        handler.handle_start(12345)

        # Storage clear_user_credentials should be called
        mock_storage.clear_user_credentials.assert_called_once_with(12345)
        # Saved session must have no credentials
        assert existing_session.credentials is None
        # User is prompted for ID / phone number
        assert mock_telegram.send_message.call_count >= 1

    def test_handle_cancel_wipes_credentials(self):
        """Verify /cancel calls clear_user_credentials and resets session."""
        mock_storage = MagicMock()
        mock_telegram = MagicMock()
        mock_reservation = MagicMock()
        mock_reminder = MagicMock()

        handler = CommandHandler(mock_storage, mock_telegram, mock_reservation, mock_reminder)
        existing_session = UserSession(chat_id=12345)
        existing_session.credentials = UserCredentials(train_id="01012345678", train_pw="secret")
        mock_storage.get_user_session.return_value = existing_session

        handler.handle_cancel(12345)

        mock_storage.clear_user_credentials.assert_called_once_with(12345)
        assert existing_session.credentials is None

    def test_confirm_payment_wipes_credentials(self):
        """Verify payment confirmation wipes credentials from storage."""
        mock_storage = MagicMock()
        mock_telegram = MagicMock()
        mock_reservation = MagicMock()

        reminder_service = PaymentReminderService(mock_storage, mock_telegram, mock_reservation)
        reminder_service.confirm_payment(12345)

        mock_storage.clear_user_credentials.assert_called_once_with(12345)
