"""Unit tests for Webhook Security (Password Masking and Auto-Deletion)."""
import unittest
from unittest.mock import MagicMock, patch
from flask import Flask
from flask_restful import Api

from api.telegram_webhook import TelegramWebhook
from models import UserSession, UserProgress


class TestWebhookSecurity(unittest.TestCase):
    """Test suite for webhook password protection."""

    def setUp(self):
        """Set up test Flask app and mocks."""
        self.app = Flask(__name__)
        self.api = Api(self.app)

        self.mock_storage = MagicMock()
        self.mock_telegram = MagicMock()
        self.mock_reservation = MagicMock()
        self.mock_payment = MagicMock()

        self.api.add_resource(
            TelegramWebhook,
            '/telebot',
            resource_class_kwargs={
                'storage': self.mock_storage,
                'telegram_service': self.mock_telegram,
                'reservation_service': self.mock_reservation,
                'payment_reminder_service': self.mock_payment
            }
        )
        self.client = self.app.test_client()

    @patch('api.telegram_webhook.logger')
    def test_password_input_masked_and_deleted(self, mock_logger):
        """Test that password message is masked in logs and delete_message is called."""
        chat_id = 99999
        message_id = 1234
        secret_pw = "superSecretPassword123!"

        # Mock session in ID_INPUT_SUCCESS state (waiting for password)
        mock_session = UserSession(
            chat_id=chat_id,
            in_progress=True,
            last_action=UserProgress.ID_INPUT_SUCCESS
        )
        self.mock_storage.get_user_session.return_value = mock_session
        self.mock_storage.get_payment_status.return_value = None
        self.mock_storage.get_multi_reservation_status.return_value = None
        self.mock_storage.get_current_seat_index.return_value = None
        self.mock_storage.is_waiting_for_admin_password.return_value = False

        payload = {
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id},
                "text": secret_pw
            }
        }

        response = self.client.post('/telebot', json=payload)
        self.assertEqual(response.status_code, 200)

        # 1. Verify delete_message was called to remove password from Telegram chat
        self.mock_telegram.delete_message.assert_called_once_with(chat_id, message_id)

        # 2. Verify logger did NOT log the plaintext password
        for call_args in mock_logger.info.call_args_list:
            log_msg = str(call_args)
            self.assertNotIn(secret_pw, log_msg)

        # 3. Verify logger logged [PROTECTED_PASSWORD]
        mock_logger.info.assert_any_call(f"Received message from chat_id={chat_id}: [PROTECTED_PASSWORD]")

    @patch('api.telegram_webhook.logger')
    def test_regular_message_not_deleted(self, mock_logger):
        """Test that normal messages are logged normally and not deleted."""
        chat_id = 99999
        message_id = 5678
        normal_text = "20260923"

        # Mock session in DATE input state
        mock_session = UserSession(
            chat_id=chat_id,
            in_progress=True,
            last_action=UserProgress.PW_INPUT_SUCCESS
        )
        self.mock_storage.get_user_session.return_value = mock_session
        self.mock_storage.get_payment_status.return_value = None
        self.mock_storage.get_multi_reservation_status.return_value = None
        self.mock_storage.get_current_seat_index.return_value = None
        self.mock_storage.is_waiting_for_admin_password.return_value = False

        payload = {
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id},
                "text": normal_text
            }
        }

        response = self.client.post('/telebot', json=payload)
        self.assertEqual(response.status_code, 200)

        # delete_message should NOT be called for non-password messages
        self.mock_telegram.delete_message.assert_not_called()

        # Regular message should be logged normally
        mock_logger.info.assert_any_call(f"Received message from chat_id={chat_id}: {normal_text}")


if __name__ == '__main__':
    unittest.main()
