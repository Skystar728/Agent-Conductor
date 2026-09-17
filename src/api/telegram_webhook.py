"""Telegram webhook API endpoint."""
from flask import request, make_response
from flask_restful import Resource

from storage.base import StorageInterface
from services import TelegramService, ReservationService, PaymentReminderService, MultiReservationReminderService
from handlers import CommandHandler, ConversationHandler
from models import PaymentStatus, UserProgress
from utils.logger import get_logger

logger = get_logger(__name__)


class TelegramWebhook(Resource):
    """
    Flask-RESTful resource for handling Telegram webhook callbacks.

    This replaces the old Index class from telebotApiHandler.py
    """

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: ReservationService,
        payment_reminder_service: PaymentReminderService,
        **kwargs
    ):
        """
        Initialize webhook handler.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
            payment_reminder_service: Payment reminder service
        """
        super().__init__(**kwargs)
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service
        self.payment_reminder = payment_reminder_service
        if hasattr(self.payment_reminder, 'reservation_service') and self.payment_reminder.reservation_service is None:
            self.payment_reminder.reservation_service = self.reservation

        # Initialize multi-reservation reminder service (singleton for thread tracking)
        self.multi_reminder = MultiReservationReminderService(storage, telegram_service)

        # Initialize handlers
        self.command_handler = CommandHandler(
            storage, telegram_service, reservation_service, payment_reminder_service
        )
        self.conversation_handler = ConversationHandler(
            storage, telegram_service, reservation_service
        )

    def post(self):
        """
        Handle POST request from Telegram webhook.

        This is called when users send messages to the bot.
        """
        try:
            data = request.json

            # Ignore edited messages and chat member updates
            if "edited_message" in data or "my_chat_member" in data:
                return make_response("OK")

            # Handle callback_query (inline button clicks) or standard messages
            if "callback_query" in data:
                try:
                    callback = data["callback_query"]
                    callback_id = callback.get("id")
                    message = callback.get("message", {})
                    chat_id = int(message.get("chat", {}).get("id"))
                    text = str(callback.get("data", "")).strip()

                    logger.info(f"🔘 [Inline Button Click] chat_id={chat_id}, callback_data='{text}'")

                    # Acknowledge callback immediately to clear Telegram loading spinner
                    if callback_id:
                        self.telegram.answer_callback_query(callback_id)
                except Exception as e:
                    logger.error(f"Invalid callback_query format: {e}")
                    return make_response("OK")
            elif "message" in data:
                # Extract standard text message
                try:
                    message = data['message']
                    text = message.get('text', '').strip()
                    chat_id = int(message['chat']['id'])
                    message_id = message.get('message_id')
                except (KeyError, ValueError) as e:
                    logger.error(f"Invalid message format: {e}")
                    return make_response("OK")

                # Get user session early to check if this is password input
                session = self.storage.get_user_session(chat_id)
                is_password_input = bool(
                    session and session.in_progress and session.last_action == UserProgress.ID_INPUT_SUCCESS
                    and text.strip() not in ("Y", "y", "예", "네", "N", "n", "아니오", "취소", "CANCEL")
                )

                if is_password_input:
                    logger.info(f"Received message from chat_id={chat_id}: [PROTECTED_PASSWORD]")
                    # Stage 4: Delete user's plain-text password from Telegram chat immediately
                    if message_id:
                        self.telegram.delete_message(chat_id, message_id)
                else:
                    logger.info(f"Received message from chat_id={chat_id}: {text}")
            else:
                return make_response("OK")

            # Session already fetched above (or fetch if not present)
            if 'session' not in locals() or session is None:
                session = self.storage.get_user_session(chat_id)
            in_progress = session.in_progress if session else False
            progress_num = session.last_action if session else 0

            logger.debug(
                f"chat_id={chat_id}, in_progress={in_progress}, "
                f"progress={progress_num}"
            )

            # Check for payment reminder active state (single reservation)
            payment_status = self.storage.get_payment_status(chat_id)
            if payment_status and payment_status.reminder_active and not payment_status.completed:
                # User sent any non-command message during payment reminder
                if text and not text.startswith('/'):
                    self.payment_reminder.confirm_payment(chat_id)
                    return make_response("OK")

            # Check for multi-reservation reminder active state (no current_seat_index set)
            # This handles the case when ALL seats are reserved but waiting for final payment
            multi_status = self.storage.get_multi_reservation_status(chat_id)
            if multi_status and multi_status.should_show_reminder():
                # Check if we're NOT in middle of random seating (no current_seat_index)
                current_seat = self.storage.get_current_seat_index(chat_id)
                if current_seat is None:
                    # All seats reserved, just waiting for payment confirmation
                    if text and not text.startswith('/'):
                        # Mark all as paid and stop reminders
                        self.multi_reminder.mark_all_paid(chat_id)
                        self.storage.reset_retry_count(chat_id)
                        self.storage.delete_last_search_params(chat_id)

                        # Send confirmation
                        self.telegram.send_message(
                            chat_id,
                            "✅ 결제 완료 확인!\n\n모든 좌석의 결제 알림이 중단되었습니다."
                        )
                        return make_response("OK")

            # Handle /cancel command first (works in any state)
            is_cancel_intent = (
                text in ("/cancel", "cancel", "Cancel", "CANCEL", "취소", "CANCEL_RESERVATION") or
                (session and session.last_action == UserProgress.FINDING_TICKET and text in ("N", "n", "아니오", "작업 취소"))
            )
            if is_cancel_intent:
                self.command_handler.handle_cancel(chat_id)
                return make_response("OK")

            # Route commands BEFORE checking random seating state
            # This allows users to use /help, /status even during payment waiting
            if self.command_handler.is_command(text):
                self.command_handler.route_command(chat_id, text)
                return make_response("OK")

            # Check if random seating in progress (waiting for payment confirmation)
            current_seat = self.storage.get_current_seat_index(chat_id)
            if current_seat is not None:  # Random seating in progress
                # ANY message confirms payment and proceeds to next seat
                logger.info(f"Payment confirmed for seat {current_seat} by user message, chat_id={chat_id}")

                # 1. Mark payment ready for background process
                self.storage.mark_payment_ready(chat_id, current_seat)

                # 2. Mark this seat as paid in multi-reservation status so reminders stop for this seat!
                self.multi_reminder.mark_seat_paid(chat_id, current_seat + 1)

                # 3. Clear current seat index so user's subsequent messages don't re-trigger this
                self.storage.set_current_seat_index(chat_id, None)

                # Send confirmation
                self.telegram.send_message(
                    chat_id,
                    f"✅ {current_seat + 1}번째 좌석 결제 확인!\n\n"
                    f"다음 좌석 예약을 시작합니다..."
                )

                return make_response("OK")

            # Check if waiting for admin password (takes priority over everything)
            if self.storage.is_waiting_for_admin_password(chat_id):
                # User is waiting to enter admin password
                if self.command_handler.handle_admin_password(chat_id, text):
                    # Successfully authenticated
                    return make_response("OK")
                else:
                    # Failed authentication
                    return make_response("OK")

            # Handle natural language reservation callback query
            if text.startswith("NL_"):
                if session and in_progress:
                    self.conversation_handler.handle_natural_callback(chat_id, text, session)
                else:
                    self.telegram.send_message(
                        chat_id,
                        "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요."
                    )
                return make_response("OK")

            # Handle conversation flow (non-command messages)
            if in_progress:
                # Handle conversation flow
                self.conversation_handler.handle_message(chat_id, text)
            else:
                # No active session and not a command
                self.telegram.send_message(
                    chat_id,
                    "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요."
                )

            return make_response("OK")

        except Exception as e:
            logger.error(f"Error handling webhook: {e}", exc_info=True)
            return make_response("OK")  # Still return OK to Telegram

    def get(self):
        """
        Handle GET request for callbacks from background processes.

        This is used by the background reservation process to notify
        the bot about reservation results.
        """
        try:
            # Extract parameters
            chat_id = request.args.get('chatId')
            msg = request.args.get('msg')
            status = request.args.get('status')
            is_multi = request.args.get('isMulti', '0')
            total_seats = request.args.get('totalSeats', '1')
            seat_strategy = request.args.get('seatStrategy', 'consecutive')

            if not all([chat_id, msg, status]):
                logger.warning("Incomplete callback parameters")
                return make_response("OK")

            chat_id = int(chat_id)
            is_multi = (is_multi == '1')
            total_seats = int(total_seats)

            logger.info(
                f"Callback from background process: chat_id={chat_id}, status={status}, "
                f"is_multi={is_multi}, total_seats={total_seats}, seat_strategy={seat_strategy}"
            )

            # Send message to user
            self.telegram.send_message(chat_id, msg)

            # Handle different status codes
            # status=0: Complete success (all reservations done)
            # status=1: Error/failure
            # status=2: Partial success (random seating intermediate notification)

            if str(status) == "2":
                # Partial reservation notification (random seating)
                logger.info(f"Partial reservation notification for chat_id={chat_id}")

                # Check if multi-reservation status exists and start reminders if needed
                multi_status = self.storage.get_multi_reservation_status(chat_id)
                if multi_status:
                    # Start multi-reservation reminders (checks for duplicates internally)
                    self.multi_reminder.start_reminders(chat_id)

                # Message already sent above, no further action needed
                # User will send payment confirmation which will be handled by POST webhook
                return make_response("OK")

            # If reservation successful (status == 0)
            if str(status) == "0":
                logger.info(f"Reservation successful for chat_id={chat_id}")

                # Save search params for auto-restart BEFORE resetting session
                session = self.storage.get_user_session(chat_id)
                if session and session.search_params:
                    self.storage.save_last_search_params(chat_id, session.search_params)
                else:
                    running_res = self.storage.get_running_reservation(chat_id)
                    if running_res and running_res.search_params:
                        self.storage.save_last_search_params(chat_id, running_res.search_params)

                # Reset user session
                if session:
                    session.reset()
                    self.storage.save_user_session(session)

                # Start appropriate payment reminders
                # For random seating, multi-reminder is already running (started on first seat)
                # Don't start duplicate reminder service
                if seat_strategy == "random":
                    logger.info(f"Random seating complete - multi-reminder already running for chat_id={chat_id}")
                    # Multi-reservation reminder was started on first partial callback (status=2)
                    # It will continue until all seats are paid or expired
                elif is_multi:
                    logger.info(f"Starting multi-reservation reminders for chat_id={chat_id}")
                    # Consecutive seating with multiple passengers
                    # TODO: This path may need multi-reminder support in the future
                    self.payment_reminder.start_reminders(chat_id)
                else:
                    logger.info(f"Starting single payment reminders for chat_id={chat_id}")
                    self.payment_reminder.start_reminders(chat_id)

                # Clean up running reservation
                self.storage.delete_running_reservation(chat_id)

                # Notify subscribers
                subscribers = self.storage.get_all_subscribers()
                if session and session.credentials:
                    user_id = session.credentials.korail_id
                    self.telegram.send_to_multiple(
                        subscribers,
                        f"{user_id}의 예약이 종료되었습니다."
                    )

            # If reservation failed / ended with error (status == 1)
            elif str(status) == "1":
                logger.info(f"Reservation ended with error/failure for chat_id={chat_id}. Cleaning up.")
                session = self.storage.get_user_session(chat_id)
                if session:
                    session.reset()
                    self.storage.save_user_session(session)
                self.storage.delete_running_reservation(chat_id)
                self.storage.delete_multi_reservation_status(chat_id)

            return make_response("OK")

        except Exception as e:
            logger.error(f"Error handling callback: {e}", exc_info=True)
            return make_response("OK")
