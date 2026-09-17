"""Payment reminder service."""
import time
import requests
import threading
from datetime import datetime

from config.settings import settings
from models import PaymentStatus
from storage.base import StorageInterface
from services.telegram_service import TelegramService, MessageTemplates
from telegramBot.messages import Messages
from utils.logger import get_logger

from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from services.reservation_service import ReservationService

logger = get_logger(__name__)


class PaymentReminderService:
    """
    Service for sending payment reminder notifications and auto-restarting on timeout.

    Sends periodic reminders to users to complete payment within the time limit.
    If payment is not confirmed after timeout, it can automatically restart search.
    """

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: Optional['ReservationService'] = None
    ):
        """
        Initialize payment reminder service.

        Args:
            storage: Storage interface for tracking payment status
            telegram_service: Telegram service for sending reminders
            reservation_service: Reservation service for auto-restarting search
        """
        self.storage = storage
        self.telegram = telegram_service
        self.reservation_service = reservation_service
        self.timeout_minutes = settings.PAYMENT_TIMEOUT_MINUTES
        self.interval_seconds = settings.PAYMENT_REMINDER_INTERVAL_SECONDS
        self.max_retries = getattr(settings, 'MAX_PAYMENT_RETRIES', 3)

    def start_reminders(self, chat_id: int) -> None:
        """
        Start sending payment reminders to a user (in background thread).

        Sends reminders at configured intervals until:
        - User confirms payment
        - Timeout expires

        Args:
            chat_id: Telegram chat ID to send reminders to
        """
        # Check if there's already an active reminder
        existing_status = self.storage.get_payment_status(chat_id)
        if existing_status and existing_status.reminder_active:
            logger.warning(
                f"Reminder already active for chat_id={chat_id}, skipping duplicate"
            )
            return

        # Initialize payment status
        payment_status = PaymentStatus(
            chat_id=chat_id,
            completed=False,
            reservation_time=datetime.now(),
            reminder_active=True
        )
        self.storage.save_payment_status(payment_status)

        logger.info(
            f"Starting payment reminders for chat_id={chat_id} in background thread, "
            f"timeout={self.timeout_minutes}min, interval={self.interval_seconds}sec"
        )

        # Start reminder loop in background thread (non-blocking)
        thread = threading.Thread(
            target=self._reminder_loop,
            args=(chat_id,),
            daemon=True
        )
        thread.start()

    def deactivate_reminders(self, chat_id: int) -> None:
        """Deactivate payment reminders for a chat ID."""
        payment_status = self.storage.get_payment_status(chat_id)
        if payment_status:
            payment_status.reminder_active = False
            self.storage.save_payment_status(payment_status)
            logger.info(f"Payment reminders deactivated for chat_id={chat_id}")

    def stop_reminders(self, chat_id: int) -> None:
        """Alias for deactivate_reminders."""
        self.deactivate_reminders(chat_id)

    def _reminder_loop(self, chat_id: int) -> None:
        """
        Reminder loop that runs in background thread.

        Args:
            chat_id: Telegram chat ID
        """
        try:
            total_seconds = self.timeout_minutes * 60

            for elapsed in range(self.interval_seconds, total_seconds + self.interval_seconds, self.interval_seconds):
                time.sleep(self.interval_seconds)

                # Check if payment completed or reminder stopped
                payment_status = self.storage.get_payment_status(chat_id)
                if not payment_status or not payment_status.reminder_active or payment_status.completed:
                    return

                # Calculate remaining time
                remaining_seconds = total_seconds - elapsed

                # Send reminder if time remaining
                if remaining_seconds > 0:
                    remaining_minutes = remaining_seconds // 60
                    remaining_secs = remaining_seconds % 60
                    self._send_reminder(chat_id, remaining_minutes, remaining_secs)

            # Final check after timeout
            payment_status = self.storage.get_payment_status(chat_id)
            if not payment_status or not payment_status.reminder_active or payment_status.completed:
                return

            self._deactivate_reminder(chat_id)

            # Check if user actually completed payment on Korail directly
            if self._verify_korail_payment(chat_id):
                self._send_completion_message(chat_id)
                self.storage.reset_retry_count(chat_id)
                self.storage.delete_last_search_params(chat_id)
                return

            # If not paid, handle automatic restart
            self._handle_timeout_restart(chat_id)

        except Exception as e:
            logger.error(f"Error in reminder loop for chat_id={chat_id}: {e}", exc_info=True)

    # Test compatibility alias
    _send_reminder_loop = _reminder_loop

    def check_payment_completed(self, chat_id: int) -> bool:
        """
        Check if payment has been completed for a chat ID.

        Args:
            chat_id: Telegram chat ID

        Returns:
            True if payment completed, False otherwise
        """
        try:
            # Try internal storage first
            payment_status = self.storage.get_payment_status(chat_id)
            if payment_status and payment_status.completed:
                return True

            # Also check via API (for compatibility)
            callback_url = f"{settings.CALLBACK_BASE_URL}/check_payment"
            params = {"chatId": chat_id}
            response = requests.get(callback_url, params=params, verify=False, timeout=5)
            return response.json().get('completed', False)

        except Exception as e:
            logger.error(f"Error checking payment status for chat_id={chat_id}: {e}")
            return False

    def confirm_payment(self, chat_id: int) -> None:
        """
        Mark payment as confirmed for a chat ID and send confirmation immediately.

        Args:
            chat_id: Telegram chat ID
        """
        payment_status = self.storage.get_payment_status(chat_id)
        if payment_status:
            payment_status.completed = True
            payment_status.reminder_active = False
            self.storage.save_payment_status(payment_status)
            logger.info(f"Payment confirmed for chat_id={chat_id}")
        else:
            # Create new status if not exists
            payment_status = PaymentStatus(
                chat_id=chat_id,
                completed=True,
                reminder_active=False
            )
            self.storage.save_payment_status(payment_status)

        # Clear retry count and backup params on successful confirmation
        self.storage.reset_retry_count(chat_id)
        self.storage.delete_last_search_params(chat_id)

        # Send completion message immediately
        self._send_completion_message(chat_id)

    def _verify_korail_payment(self, chat_id: int) -> bool:
        """
        Verify with Korail API if tickets were actually paid and issued.
        """
        try:
            session = self.storage.get_user_session(chat_id)
            if not session or not session.credentials:
                return False

            from korail2 import Korail
            korail = Korail(
                session.credentials.korail_id,
                session.credentials.korail_pw,
                auto_login=True
            )
            tickets = korail.tickets()
            if tickets:
                last_params = self.storage.get_last_search_params(chat_id)
                if last_params:
                    # Match dep_date and route if possible
                    for t in tickets:
                        if hasattr(t, 'dep_date') and t.dep_date == last_params.dep_date:
                            logger.info(f"Verified payment on Korail for chat_id={chat_id}: ticket={t}")
                            return True
                else:
                    logger.info(f"User has paid tickets on Korail for chat_id={chat_id}")
                    return True

            return False
        except Exception as e:
            logger.warning(f"Could not verify Korail tickets for chat_id={chat_id}: {e}")
            return False

    def _cleanup_unpaid_korail_reservations(self, chat_id: int) -> None:
        """Cancel any unpaid reservations on Korail for this user."""
        try:
            session = self.storage.get_user_session(chat_id)
            if not session or not session.credentials:
                return

            from korail2 import Korail
            korail = Korail(
                session.credentials.korail_id,
                session.credentials.korail_pw,
                auto_login=True
            )
            reserves = korail.reservations()
            if reserves:
                for rsv in reserves:
                    try:
                        korail.cancel(rsv)
                        logger.info(f"Cancelled unpaid reservation on Korail for chat_id={chat_id}: {rsv}")
                    except Exception as ce:
                        logger.warning(f"Failed to cancel unpaid reservation: {ce}")
        except Exception as e:
            logger.debug(f"No reservations cleaned up or error: {e}")

    def _handle_timeout_restart(self, chat_id: int) -> None:
        """
        Handle automatic search restart after payment timeout.
        """
        try:
            # 1. Clear any remaining unpaid reservations on Korail to avoid duplicate booking collisions
            self._cleanup_unpaid_korail_reservations(chat_id)

            # 2. Check retry count and last search params
            last_params = self.storage.get_last_search_params(chat_id)
            retry_count = self.storage.increment_retry_count(chat_id)

            if retry_count <= self.max_retries and last_params and self.reservation_service:
                logger.info(f"Auto-restarting search on payment timeout for chat_id={chat_id} (retry {retry_count}/{self.max_retries})")
                self.telegram.send_message(
                    chat_id,
                    f"⏱ 10분 동안 결제가 확인되지 않아 좌석 탐색을 자동으로 다시 시작합니다!\n"
                    f"(자동 재시도 {retry_count}/{self.max_retries}회)"
                )
                self.reservation_service.restart_reservation(chat_id, last_params)
            else:
                if retry_count > self.max_retries:
                    logger.info(f"Max retries reached ({self.max_retries}) for chat_id={chat_id}. Ending search.")
                    self.telegram.send_message(
                        chat_id,
                        f"⏱ 결제 기한 초과로 최대 재시도 횟수({self.max_retries}회)에 도달하여 탐색을 종료합니다.\n\n"
                        f"다시 시작하시려면 /start 를 입력해주세요."
                    )
                else:
                    self._send_timeout_message(chat_id)

                self.storage.reset_retry_count(chat_id)
                self.storage.delete_last_search_params(chat_id)

        except Exception as e:
            logger.error(f"Error handling timeout restart for chat_id={chat_id}: {e}", exc_info=True)
            self._send_timeout_message(chat_id)

    def _send_reminder(self, chat_id: int, minutes: int, seconds: int) -> None:
        """Send a payment reminder message."""
        message = MessageTemplates.payment_reminder(minutes, seconds)
        self.telegram.send_message(chat_id, message)
        logger.debug(f"Sent payment reminder to chat_id={chat_id}, remaining={minutes}m {seconds}s")

    def _send_completion_message(self, chat_id: int) -> None:
        """Send reminder stopped message (user sent a message)."""
        self.telegram.send_message(chat_id, Messages.PAYMENT_REMINDER_STOPPED)
        logger.info(f"Sent reminder stopped message to chat_id={chat_id}")

    def _send_timeout_message(self, chat_id: int) -> None:
        """Send reminder timeout message (10 minutes elapsed)."""
        self.telegram.send_message(chat_id, Messages.PAYMENT_REMINDER_TIMEOUT)
        logger.warning(f"Payment reminder timeout for chat_id={chat_id}")

    def _deactivate_reminder(self, chat_id: int) -> None:
        """Deactivate reminder for a chat ID."""
        payment_status = self.storage.get_payment_status(chat_id)
        if payment_status:
            payment_status.reminder_active = False
            self.storage.save_payment_status(payment_status)
            logger.info(f"Deactivated reminder for chat_id={chat_id}")

