import pytest
from unittest.mock import MagicMock, patch
from models.user import UserProgress, UserSession
from services.telegram_service import TelegramService
from handlers.command_handler import CommandHandler
from handlers.conversation_handler import ConversationHandler
from services.reservation_service import ReservationService


class TestMenuAndRestart:
    def test_setup_bot_commands(self):
        """Test setup_bot_commands calls setMyCommands and setChatMenuButton."""
        service = TelegramService("dummy_token")
        with patch.object(service.session, "post") as mock_post:
            mock_res = MagicMock()
            mock_res.raise_for_status.return_value = None
            mock_post.return_value = mock_res

            res = service.setup_bot_commands()
            assert res is True
            assert mock_post.call_count == 2
            # Verify first call was setMyCommands
            first_call_url = mock_post.call_args_list[0][0][0]
            assert "setMyCommands" in first_call_url

    def test_command_cancel_includes_restart_button(self):
        """Test /cancel includes inline restart button."""
        mock_storage = MagicMock()
        mock_telegram = MagicMock()
        mock_telegram.build_inline_keyboard.side_effect = TelegramService.build_inline_keyboard
        mock_reservation = MagicMock()
        mock_reservation.cancel_reservation.return_value = False
        mock_payment_reminder = MagicMock()

        handler = CommandHandler(mock_storage, mock_telegram, mock_reservation, mock_payment_reminder)
        handler.handle_cancel(12345)

        mock_telegram.send_message.assert_called()
        _, kwargs = mock_telegram.send_message.call_args
        assert "reply_markup" in kwargs
        markup = kwargs["reply_markup"]
        assert markup["inline_keyboard"][0][0]["callback_data"] == "/start"

    def test_conversation_cancel_includes_restart_button(self):
        """Test conversation cancellation includes restart button."""
        mock_storage = MagicMock()
        mock_telegram = MagicMock()
        mock_telegram.build_inline_keyboard.side_effect = TelegramService.build_inline_keyboard
        mock_reservation = MagicMock()

        conv_handler = ConversationHandler(mock_storage, mock_telegram, mock_reservation)
        session = UserSession(chat_id=12345, in_progress=True)
        session.last_action = UserProgress.ID_INPUT_SUCCESS
        mock_storage.get_user_session.return_value = session

        conv_handler.handle_message(12345, "N")
        mock_telegram.send_message.assert_called()
        _, kwargs = mock_telegram.send_message.call_args
        assert "reply_markup" in kwargs
        markup = kwargs["reply_markup"]
        assert markup["inline_keyboard"][0][0]["callback_data"] == "/start"

    def test_help_message_does_not_contain_admin_features(self):
        """Test /help output contains only user features and no admin commands."""
        from telegramBot.messages import Messages
        help_msg = Messages.HELP
        assert "/start" in help_msg
        assert "/cancel" in help_msg
        assert "/edit" in help_msg
        assert "/seat" in help_msg
        assert "관리자 명령어" not in help_msg
        assert "/subscribe" not in help_msg
        assert "/allusers" not in help_msg
        assert "/cancelall" not in help_msg
        assert "/flushredis" not in help_msg
