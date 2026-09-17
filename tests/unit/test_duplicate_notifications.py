"""Unit tests for preventing duplicate notifications on /cancel and payment confirmations."""
import unittest
from unittest.mock import MagicMock

from handlers.command_handler import CommandHandler
from services.payment_reminder_service import PaymentReminderService
from models import PaymentStatus


class TestDuplicateNotifications(unittest.TestCase):
    """Verify that /cancel and payment confirmation do not send duplicate notifications."""

    def test_cancel_when_reservation_was_running_sends_once(self):
        """When a running reservation exists, cancel_reservation returns True and sends message once."""
        storage = MagicMock()
        telegram = MagicMock()
        reservation = MagicMock()
        payment_reminder = MagicMock()

        # cancel_reservation returns True (indicating it handled process kill and sent message)
        reservation.cancel_reservation.return_value = True

        handler = CommandHandler(storage, telegram, reservation, payment_reminder)
        handler.handle_cancel(chat_id=12345)

        # telegram.send_message should NOT be called by handle_cancel since reservation_service already sent it
        telegram.send_message.assert_not_called()
        reservation.cancel_reservation.assert_called_once_with(12345)

    def test_cancel_when_no_reservation_running_sends_once(self):
        """When no reservation is running, cancel_reservation returns False and handle_cancel sends message once."""
        storage = MagicMock()
        telegram = MagicMock()
        reservation = MagicMock()
        payment_reminder = MagicMock()

        # cancel_reservation returns False (no running reservation)
        reservation.cancel_reservation.return_value = False

        handler = CommandHandler(storage, telegram, reservation, payment_reminder)
        handler.handle_cancel(chat_id=12345)

        # telegram.send_message should be called exactly once
        telegram.send_message.assert_called_once()
        assert telegram.send_message.call_args[0] == (12345, "✅ 예약이 취소되었습니다.")

    def test_single_payment_confirmation_sends_once_immediately(self):
        """When user confirms payment, confirm_payment sends completion message immediately and once."""
        storage = MagicMock()
        telegram = MagicMock()

        payment_status = PaymentStatus(
            chat_id=12345,
            completed=False,
            reminder_active=True
        )
        storage.get_payment_status.return_value = payment_status

        service = PaymentReminderService(storage, telegram)
        service.confirm_payment(12345)

        # Status updated
        self.assertTrue(payment_status.completed)
        self.assertFalse(payment_status.reminder_active)

        # Message sent once immediately
        telegram.send_message.assert_called_once()
        self.assertIn("리마인더가 중단되었습니다", telegram.send_message.call_args[0][1])
