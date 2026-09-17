"""Unit tests for Telegram inline keyboard buttons and callback_query routing."""
import unittest
from unittest.mock import MagicMock, patch

from services.telegram_service import TelegramService


class TestInlineButtons(unittest.TestCase):
    """Test suite for inline keyboard buttons and webhook callback handling."""

    def setUp(self):
        self.telegram = TelegramService(bot_token="TEST_TOKEN")

    def test_build_inline_keyboard(self):
        """Test constructing Telegram inline keyboard markup."""
        rows = [
            [("1️⃣ 일반실 우선", "1"), ("2️⃣ 일반실만", "2")],
            [("3️⃣ 특실 우선", "3"), ("4️⃣ 특실만", "4")]
        ]
        markup = self.telegram.build_inline_keyboard(rows)

        self.assertIn("inline_keyboard", markup)
        self.assertEqual(len(markup["inline_keyboard"]), 2)
        self.assertEqual(markup["inline_keyboard"][0][0]["text"], "1️⃣ 일반실 우선")
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "1")
        self.assertEqual(markup["inline_keyboard"][1][1]["text"], "4️⃣ 특실만")
        self.assertEqual(markup["inline_keyboard"][1][1]["callback_data"], "4")

    def test_send_message_with_reply_markup(self):
        """Test sending message with inline keyboard JSON payload."""
        telegram = TelegramService(bot_token="TEST_TOKEN")
        mock_post = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp
        telegram.session.post = mock_post

        markup = {"inline_keyboard": [[{"text": "Yes", "callback_data": "Y"}]]}
        success = telegram.send_message(12345, "Hello", reply_markup=markup)

        self.assertTrue(success)
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["chat_id"], 12345)
        self.assertEqual(kwargs["json"]["text"], "Hello")
        self.assertEqual(kwargs["json"]["reply_markup"], markup)

    def test_answer_callback_query(self):
        """Test answerCallbackQuery API call."""
        telegram = TelegramService(bot_token="TEST_TOKEN")
        mock_post = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp
        telegram.session.post = mock_post

        success = telegram.answer_callback_query("cb_123", text="선택됨")
        self.assertTrue(success)
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["callback_query_id"], "cb_123")
        self.assertEqual(kwargs["json"]["text"], "선택됨")


if __name__ == "__main__":
    unittest.main()
