"""Unit tests for automatic reservation restart on 10-minute payment timeout."""
import unittest
from unittest.mock import MagicMock, patch

from services.payment_reminder_service import PaymentReminderService
from models import PaymentStatus, TrainSearchParams


class TestAutoRestartOnTimeout(unittest.TestCase):
    """Test suite for payment timeout auto-restart logic."""

    def setUp(self):
        self.storage = MagicMock()
        self.telegram = MagicMock()
        self.reservation_service = MagicMock()
        self.service = PaymentReminderService(
            self.storage,
            self.telegram,
            self.reservation_service
        )
        self.service.max_retries = 3

        self.sample_params = TrainSearchParams(
            dep_date="20260925",
            src_locate="서울",
            dst_locate="부산",
            dep_time="090000",
            max_dep_time="1200",
            train_type="100",
            train_type_display="KTX",
            special_option="GENERAL_FIRST",
            special_option_display="일반실 우선",
            passenger_count=1,
            seat_strategy="consecutive"
        )

    @patch.object(PaymentReminderService, '_cleanup_unpaid_korail_reservations')
    def test_auto_restart_when_retries_available(self, mock_cleanup):
        """When timeout occurs and retry count <= 3, restart_reservation should be called."""
        chat_id = 12345
        self.storage.get_last_search_params.return_value = self.sample_params
        self.storage.increment_retry_count.return_value = 1

        self.service._handle_timeout_restart(chat_id)

        # Should cleanup unpaid reservations on Korail
        mock_cleanup.assert_called_once_with(chat_id)

        # Should increment retry count
        self.storage.increment_retry_count.assert_called_once_with(chat_id)

        # Should send restart message to user
        self.telegram.send_message.assert_called_once()
        msg = self.telegram.send_message.call_args[0][1]
        self.assertIn("부재중", msg)
        self.assertIn("재시작합니다", msg)
        self.assertIn("1/3회", msg)

        # Should trigger restart_reservation
        self.reservation_service.restart_reservation.assert_called_once_with(chat_id, self.sample_params)

    @patch.object(PaymentReminderService, '_cleanup_unpaid_korail_reservations')
    def test_stop_when_max_retries_exceeded(self, mock_cleanup):
        """When retry count exceeds max_retries, do not restart and notify user."""
        chat_id = 12345
        self.storage.get_last_search_params.return_value = self.sample_params
        self.storage.increment_retry_count.return_value = 4  # Exceeded 3

        self.service._handle_timeout_restart(chat_id)

        # Should NOT call restart_reservation
        self.reservation_service.restart_reservation.assert_not_called()

        # Should send final termination message
        self.telegram.send_message.assert_called_once()
        msg = self.telegram.send_message.call_args[0][1]
        self.assertIn("최대 재시도 횟수(3회)에 도달하여 탐색을 종료합니다", msg)

        # Should reset retry count and delete backup params
        self.storage.reset_retry_count.assert_called_once_with(chat_id)
        self.storage.delete_last_search_params.assert_called_once_with(chat_id)

    @patch.object(PaymentReminderService, '_verify_korail_payment')
    @patch.object(PaymentReminderService, '_handle_timeout_restart')
    def test_bypass_restart_if_user_already_paid(self, mock_restart, mock_verify):
        """If Korail verification detects tickets paid, do not restart and complete."""
        chat_id = 12345
        mock_verify.return_value = True

        status = PaymentStatus(chat_id=chat_id, completed=False, reminder_active=True)
        self.storage.get_payment_status.return_value = status

        with patch('time.sleep', return_value=None):
            self.service.timeout_minutes = 0  # Trigger timeout immediately
            self.service.interval_seconds = 10
            self.service._reminder_loop(chat_id)

        # Verify called
        mock_verify.assert_called_once_with(chat_id)

        # Restart should NOT be called
        mock_restart.assert_not_called()

        # Should send completion message
        self.telegram.send_message.assert_called_once()
        msg = self.telegram.send_message.call_args[0][1]
        self.assertIn("리마인더가 중단되었습니다", msg)

        # Storage cleanup
        self.storage.reset_retry_count.assert_called_once_with(chat_id)
        self.storage.delete_last_search_params.assert_called_once_with(chat_id)
