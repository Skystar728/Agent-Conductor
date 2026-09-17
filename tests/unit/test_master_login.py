"""Unit tests for Master Login authorization."""
import pytest
from unittest.mock import MagicMock, patch
from models.user import UserProgress, UserSession
from handlers.conversation_handler import ConversationHandler
from config.settings import settings
from telegramBot.messages import Messages


class TestMasterLoginAuthorization:
    @pytest.fixture
    def setup_handler(self):
        mock_storage = MagicMock()
        mock_telegram = MagicMock()
        mock_reservation = MagicMock()
        mock_storage.is_admin_authenticated.return_value = False
        handler = ConversationHandler(mock_storage, mock_telegram, mock_reservation)
        return handler, mock_storage, mock_telegram

    def test_unauthorized_chat_id_blocked(self, setup_handler):
        """Unauthorized user sending 마스터로그인 must be rejected."""
        handler, mock_storage, mock_telegram = setup_handler
        unauth_chat_id = 99999999
        session = UserSession(chat_id=unauth_chat_id, in_progress=True, last_action=UserProgress.STARTED)
        mock_storage.get_user_session.return_value = session

        with patch.object(settings, "get_master_user_list", return_value=["11111", "22222"]):
            handler.handle_message(unauth_chat_id, "마스터로그인")

            mock_telegram.send_message.assert_called_once_with(
                unauth_chat_id,
                Messages.ERROR_MASTER_LOGIN_UNAUTHORIZED
            )
            # Session should NOT advance to PW_INPUT_SUCCESS
            assert session.last_action != UserProgress.PW_INPUT_SUCCESS

    def test_authorized_chat_id_allowed(self, setup_handler):
        """Authorized user in MASTER_USER_LIST succeeds."""
        handler, mock_storage, mock_telegram = setup_handler
        auth_chat_id = 7831580347
        session = UserSession(chat_id=auth_chat_id, in_progress=True, last_action=UserProgress.STARTED)
        mock_storage.get_user_session.return_value = session

        with patch.object(settings, "get_master_user_list", return_value=["7831580347"]), \
             patch.object(settings, "TRAIN_ADMIN_USER_ID", "master_user"), \
             patch.object(settings, "TRAIN_ADMIN_PASSWORD", "master_pw"), \
             patch("services.korail_service.KorailService.login", return_value=True):
            handler.handle_message(auth_chat_id, "마스터로그인")

            assert session.last_action == UserProgress.PW_INPUT_SUCCESS
            mock_storage.save_user_session.assert_called()

    def test_admin_authenticated_user_allowed_without_master_list(self, setup_handler):
        """User authenticated via /admin is authorized even if not in master list."""
        handler, mock_storage, mock_telegram = setup_handler
        admin_chat_id = 55555
        session = UserSession(chat_id=admin_chat_id, in_progress=True, last_action=UserProgress.STARTED)
        mock_storage.get_user_session.return_value = session
        mock_storage.is_admin_authenticated.return_value = True

        with patch.object(settings, "get_master_user_list", return_value=[]), \
             patch.object(settings, "TRAIN_ADMIN_USER_ID", "master_user"), \
             patch.object(settings, "TRAIN_ADMIN_PASSWORD", "master_pw"), \
             patch("services.korail_service.KorailService.login", return_value=True):
            handler.handle_message(admin_chat_id, "마스터로그인")

            assert session.last_action == UserProgress.PW_INPUT_SUCCESS

    def test_empty_master_user_list_blocks_by_default(self, setup_handler):
        """Empty MASTER_USER_LIST fails closed (strict security)."""
        handler, mock_storage, mock_telegram = setup_handler
        random_chat_id = 12345
        session = UserSession(chat_id=random_chat_id, in_progress=True, last_action=UserProgress.STARTED)
        mock_storage.get_user_session.return_value = session

        with patch.object(settings, "get_master_user_list", return_value=[]):
            handler.handle_message(random_chat_id, "마스터로그인")

            mock_telegram.send_message.assert_called_once_with(
                random_chat_id,
                Messages.ERROR_MASTER_LOGIN_UNAUTHORIZED
            )
