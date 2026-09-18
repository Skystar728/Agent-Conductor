"""Conversation flow handler for reservation process."""
from datetime import datetime, timezone, timedelta
from typing import Optional
from korail2 import TrainType, ReserveOption

from config.settings import settings
from models import UserSession, UserProgress, UserCredentials, TrainSearchParams
from storage.base import StorageInterface
from services import (
    TelegramService,
    KorailService,
    ReservationService,
    MessageTemplates,
    AIAgentService,
)
from telegramBot.messages import Messages
from utils.validators import InputValidator
from utils.logger import get_logger

logger = get_logger(__name__)


class ConversationHandler:
    """Handles multi-step conversation flow for train reservation."""

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService,
        reservation_service: ReservationService,
        ai_agent_service: Optional[AIAgentService] = None
    ):
        """
        Initialize conversation handler.

        Args:
            storage: Storage interface
            telegram_service: Telegram messaging service
            reservation_service: Reservation service
            ai_agent_service: Optional AI agent service for natural language parsing
        """
        self.storage = storage
        self.telegram = telegram_service
        self.reservation = reservation_service
        self.ai_agent = ai_agent_service or AIAgentService(storage)

    def handle_message(self, chat_id: int, text: str) -> None:
        """
        Handle user message based on current conversation state.

        Args:
            chat_id: Telegram chat ID
            text: User's message text
        """
        # Get user session
        session = self.storage.get_user_session(chat_id)
        if not session:
            logger.warning(f"No session found for chat_id={chat_id}")
            self.telegram.send_message(
                chat_id,
                "[진행중인 예약프로세스가 없습니다]\n/start 를 입력하여 작업을 시작하세요."
            )
            return

        # Check if user is responding to an active HITL decision request
        hitl_request = self.storage.get_hitl_request(chat_id)
        if hitl_request:
            if self._handle_hitl_input(chat_id, text, hitl_request):
                return

        # Check if already finding ticket
        if session.last_action == UserProgress.FINDING_TICKET:
            self._handle_running_reservation_input(chat_id, text, session)
            return

        # Check for master admin login anytime
        if text in (settings.ADMIN_MAGIC_STRING, "마스터로그인", "관리자로그인"):
            self._handle_admin_login(chat_id, session)
            return

        # Check for natural language callback query
        if text.startswith("NL_"):
            self.handle_natural_callback(chat_id, text, session)
            return

        # Route to appropriate handler based on progress
        progress = session.last_action

        if progress == UserProgress.INIT:
            markup = self.telegram.build_inline_keyboard([[("🚀 새 여정 시작 (/start)", "/start")]])
            self.telegram.send_message(
                chat_id,
                "🚂 Agent-Conductor\n\n진행 중인 여정 조율이 없습니다.\n/start 를 입력하거나 아래 버튼을 눌러 여정을 시작하세요.",
                reply_markup=markup
            )
            return

        elif progress == UserProgress.STARTED:
            self._handle_start_confirmation(chat_id, text, session)
        elif progress == UserProgress.START_ACCEPTED:
            self._handle_phone_input(chat_id, text, session)
        elif progress == UserProgress.ID_INPUT_SUCCESS:
            self._handle_password_input(chat_id, text, session)
        elif progress == UserProgress.PW_INPUT_SUCCESS:
            if text.isdigit() and len(text) == 8:
                self._handle_date_input(chat_id, text, session)
            else:
                is_valid_date, _ = InputValidator.validate_date(text)
                if is_valid_date:
                    self._handle_date_input(chat_id, text, session)
                else:
                    self._handle_natural_reservation_input(chat_id, text, session)
        elif progress == UserProgress.DATE_INPUT_SUCCESS:
            self._handle_src_station_input(chat_id, text, session)
        elif progress == UserProgress.SRC_LOCATE_INPUT_SUCCESS:
            self._handle_dst_station_input(chat_id, text, session)
        elif progress == UserProgress.DST_LOCATE_INPUT_SUCCESS:
            self._handle_dep_time_input(chat_id, text, session)
        elif progress == UserProgress.DEP_TIME_INPUT_SUCCESS:
            self._handle_max_dep_time_input(chat_id, text, session)
        elif progress == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS:
            self._handle_train_type_input(chat_id, text, session)
        elif progress == UserProgress.TRAIN_TYPE_INPUT_SUCCESS:
            self._handle_special_option_input(chat_id, text, session)
        elif progress == UserProgress.SPECIAL_INPUT_SUCCESS:
            self._handle_passenger_count_input(chat_id, text, session)
        elif progress == UserProgress.PASSENGER_COUNT_INPUT_SUCCESS:
            self._handle_seat_strategy_input(chat_id, text, session)
        elif progress == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS:
            self._handle_final_confirmation(chat_id, text, session)
        elif progress == UserProgress.NATURAL_INPUT_CONFIRMATION:
            if text in ("Y", "y", "예", "네", "예약 시작", "시작", "확인"):
                slots = session.train_info.get("nl_slots", {})
                if not slots.get("missing_slots"):
                    self._start_reservation(chat_id, session)
                else:
                    self._render_natural_confirmation_or_slots(chat_id, session, slots)
            elif text in ("N", "n", "아니오", "취소"):
                session.reset()
                self.storage.save_user_session(session)
                markup = self.telegram.build_inline_keyboard([[("🚀 새 여정 시작 (/start)", "/start")]])
                self.telegram.send_message(chat_id, Messages.CANCELLED_BY_USER, reply_markup=markup)
            else:
                self._handle_natural_reservation_input(chat_id, text, session)
        elif progress == UserProgress.EDIT_SELECT_FIELD:
            self._handle_edit_select_field(chat_id, text, session)
        elif progress == UserProgress.EDIT_INPUT_VALUE:
            self._handle_edit_input_value(chat_id, text, session)
        else:
            logger.error(f"Unknown progress state: {progress}")
            self.telegram.send_message(
                chat_id,
                "이상이 발생했습니다. /cancel 이나 /start 를 통해 다시 프로그램을 시작해주세요."
            )

    def _handle_start_confirmation(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle initial start confirmation (Y/N)."""
        # Check for account change request
        if text == "CHANGE_ACCOUNT":
            session.credentials = None
            session.in_progress = True
            session.last_action = UserProgress.START_ACCEPTED
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.request_phone_number())
            return

        # Check for master admin login
        if text in (settings.ADMIN_MAGIC_STRING, "마스터로그인", "관리자로그인"):
            self._handle_admin_login(chat_id, session)
            return

        is_yes, error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            # Check if user already has saved credentials
            if session.credentials and session.credentials.korail_id and session.credentials.korail_pw:
                session.in_progress = True
                session.last_action = UserProgress.PW_INPUT_SUCCESS
                self.storage.save_user_session(session)
                markup = self.telegram.build_inline_keyboard([
                    [("🔄 다른 계정으로 로그인", "CHANGE_ACCOUNT")]
                ])
                self.telegram.send_message(
                    chat_id,
                    f"🚂 Agent-Conductor\n\n"
                    f"✅ 저장된 계정(`{session.credentials.korail_id}`)으로 바로 진행합니다.\n\n"
                    f"출발일(예: 2026-09-20)을 입력하시거나, 자연어로 일정을 말씀해 주세요.\n"
                    f"예: '내일 저녁 6시 서울에서 부산 2명'",
                    reply_markup=markup
                )
                return

            session.last_action = UserProgress.START_ACCEPTED
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.request_phone_number())
        elif is_yes is False:
            session.reset()
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([[("🚀 새 여정 시작 (/start)", "/start")]])
            self.telegram.send_message(chat_id, Messages.CANCEL_START_CONFIRMATION, reply_markup=markup)
        else:
            self.telegram.send_message(chat_id, error)

    def _is_master_login_authorized(self, chat_id: int, session: UserSession) -> bool:
        """Check if chat_id is authorized to execute master login."""
        # 1. Check if user already authenticated as admin via /admin command
        if self.storage.is_admin_authenticated(chat_id):
            return True

        # 2. Check if chat_id or registered identifier is in MASTER_USER_LIST
        user_identifier = None
        if session and session.credentials:
            user_identifier = session.credentials.train_id or session.credentials.korail_id

        return settings.is_master_user_allowed(chat_id, user_identifier)

    def _handle_admin_login(self, chat_id: int, session: UserSession) -> None:
        """Handle master admin login using environment variables."""
        if not self._is_master_login_authorized(chat_id, session):
            logger.warning(f"Unauthorized master login attempt blocked for chat_id={chat_id}")
            self.telegram.send_message(chat_id, Messages.ERROR_MASTER_LOGIN_UNAUTHORIZED)
            return

        username = settings.TRAIN_ADMIN_USER_ID or settings.KORAIL_ADMIN_USER_ID
        password = settings.TRAIN_ADMIN_PASSWORD or settings.KORAIL_ADMIN_PASSWORD

        if not username or not password:
            session.reset()
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, Messages.ERROR_ADMIN_ENV)
            return

        # Try login
        korail = KorailService()
        if korail.login(username, password):
            session.credentials = UserCredentials(korail_id=username, korail_pw=password)
            session.last_action = UserProgress.PW_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.login_success())
        else:
            session.reset()
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, Messages.ERROR_ADMIN_LOGIN)

    def _handle_phone_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle phone number input."""
        is_valid, error = InputValidator.validate_phone_number(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error + " 다시 입력 바랍니다.")
            return

        # Check allow list
        if not settings.is_user_allowed(text):
            # Notify subscribers
            subscribers = self.storage.get_all_subscribers()
            self.telegram.send_to_multiple(
                subscribers,
                f"{text}가 구독자 목록에 없어서 실행에 실패했음."
            )

            session.reset()
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.not_in_allow_list())
            return

        # Save phone number
        if not session.credentials:
            session.credentials = UserCredentials(korail_id=text, korail_pw="")
        else:
            session.credentials.korail_id = text

        session.last_action = UserProgress.ID_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, MessageTemplates.request_password())

    def _handle_password_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle password input and login."""
        cleaned = text.strip()

        # Check if user wants to cancel
        if cleaned in ("N", "n", "아니오", "취소", "CANCEL"):
            session.reset()
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([[("🚀 새 여정 시작 (/start)", "/start")]])
            self.telegram.send_message(chat_id, Messages.CANCELLED_BY_USER, reply_markup=markup)
            return

        # Check if user wants to re-enter ID/phone from scratch
        if cleaned in ("Y", "y", "예", "네", "RETRY_ID"):
            session.last_action = UserProgress.START_ACCEPTED
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.request_phone_number())
            return

        # Validate password
        is_valid, error = InputValidator.validate_password(text)
        if not is_valid:
            self.telegram.send_message(chat_id, error + " 다시 입력 바랍니다.")
            return

        username = session.credentials.korail_id
        password = text

        # Update credentials
        session.credentials.korail_pw = password
        self.storage.save_user_session(session)

        # Try login
        korail = KorailService()
        if korail.login(username, password):
            session.last_action = UserProgress.PW_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, MessageTemplates.login_success())
        else:
            # Login failed - ask for retry with inline buttons
            markup = self.telegram.build_inline_keyboard([
                [("📱 계정정보 다시 입력 (Y)", "Y"), ("❌ 작업 취소 (N)", "N")]
            ])
            self.telegram.send_message(
                chat_id,
                MessageTemplates.login_failure(username),
                reply_markup=markup
            )
            # Don't change state - wait for retry input or Y/N

    def _handle_date_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle departure date input."""
        is_valid, error = InputValidator.validate_date(text)

        if not is_valid:
            self.telegram.send_message(
                chat_id,
                f"{error}\n출발 희망일 8자를 입력해주십시오.\n(ex_ 20260920) <- 2026년 9월 20일"
            )
            return

        session.train_info['depDate'] = text
        session.last_action = UserProgress.DATE_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, MessageTemplates.request_departure_station())

    def _handle_src_station_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle source station input."""
        from utils.station_codes import resolve_station_name

        is_valid, error = InputValidator.validate_station_name(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        session.train_info['srcLocate'] = resolve_station_name(text)
        session.last_action = UserProgress.SRC_LOCATE_INPUT_SUCCESS
        self.storage.save_user_session(session)
        self.telegram.send_message(chat_id, MessageTemplates.request_arrival_station())

    def _handle_dst_station_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle destination station input."""
        from utils.station_codes import resolve_station_name

        is_valid, error = InputValidator.validate_station_name(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        resolved_dst = resolve_station_name(text)
        src_station = session.train_info.get('srcLocate')
        if src_station and resolved_dst == src_station:
            self.telegram.send_message(
                chat_id,
                f"❌ 출발역({src_station})과 도착역이 동일할 수 없습니다.\n다른 도착역을 입력해주세요."
            )
            return

        session.train_info['dstLocate'] = resolved_dst
        session.last_action = UserProgress.DST_LOCATE_INPUT_SUCCESS
        self.storage.save_user_session(session)

        self.telegram.send_message(chat_id, Messages.REQUEST_DST_STATION)

    def _handle_dep_time_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle departure time input."""
        is_valid, error = InputValidator.validate_time(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        session.train_info['depTime'] = text + "00"  # Add seconds
        session.last_action = UserProgress.DEP_TIME_INPUT_SUCCESS
        self.storage.save_user_session(session)

        self.telegram.send_message(chat_id, Messages.REQUEST_DEP_TIME)

    def _handle_max_dep_time_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle max departure time input."""
        # Allow 2400 as special value
        if text == "2400":
            is_valid = True
        else:
            is_valid, error = InputValidator.validate_time(text)
            if not is_valid:
                self.telegram.send_message(chat_id, error)
                return

        # Ensure max departure time is not earlier than departure time
        start_time_hhmm = session.train_info.get('depTime', '000000')[:4]
        if text != "2400" and int(text) < int(start_time_hhmm):
            self.telegram.send_message(
                chat_id,
                f"❌ 검색 종료 시각({text})은 검색 시작 시각({start_time_hhmm})보다 이후여야 합니다.\n"
                f"다시 입력해주세요. (시간 제한 없이 검색하려면 2400 입력)"
            )
            return

        session.train_info['maxDepTime'] = text
        session.last_action = UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        self.storage.save_user_session(session)

        markup = self.telegram.build_inline_keyboard([
            [("🚄 1. Flagship Train (플래그십)", "1")],
            [("🚅 2. Entry Train (엔트리 / 전체)", "2")]
        ])
        self.telegram.send_message(chat_id, Messages.REQUEST_TRAIN_TYPE, reply_markup=markup)

    def _handle_train_type_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle train type selection."""
        is_valid, error = InputValidator.validate_train_type_choice(text)

        if not is_valid:
            markup = self.telegram.build_inline_keyboard([
                [("🚄 1. Flagship Train (플래그십)", "1")],
                [("🚅 2. Entry Train (엔트리 / 전체)", "2")]
            ])
            self.telegram.send_message(chat_id, error, reply_markup=markup)
            return

        if text == "1":
            session.train_info['trainType'] = "FLAGSHIP"
            session.train_info['trainTypeShow'] = "Flagship (플래그십)"
        else:
            session.train_info['trainType'] = "ENTRY"
            session.train_info['trainTypeShow'] = "Entry (엔트리)"

        session.last_action = UserProgress.TRAIN_TYPE_INPUT_SUCCESS
        self.storage.save_user_session(session)

        markup = self.telegram.build_inline_keyboard([
            [("1️⃣ 일반실 우선", "1"), ("2️⃣ 일반실만", "2")],
            [("3️⃣ 특실 우선", "3"), ("4️⃣ 특실만", "4")]
        ])
        self.telegram.send_message(chat_id, Messages.REQUEST_SEAT_TYPE, reply_markup=markup)

    def _handle_special_option_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle special seat option selection."""
        is_valid, error = InputValidator.validate_special_option_choice(text)

        if not is_valid:
            markup = self.telegram.build_inline_keyboard([
                [("1️⃣ 일반실 우선", "1"), ("2️⃣ 일반실만", "2")],
                [("3️⃣ 특실 우선", "3"), ("4️⃣ 특실만", "4")]
            ])
            self.telegram.send_message(chat_id, error, reply_markup=markup)
            return

        option_map = {
            "1": (ReserveOption.GENERAL_FIRST, "GENERAL_FIRST"),
            "2": (ReserveOption.GENERAL_ONLY, "GENERAL_ONLY"),
            "3": (ReserveOption.SPECIAL_FIRST, "SPECIAL_FIRST"),
            "4": (ReserveOption.SPECIAL_ONLY, "SPECIAL_ONLY"),
        }

        option, option_display = option_map[text]
        session.train_info['specialInfo'] = str(option)
        session.train_info['specialInfoShow'] = option_display

        session.last_action = UserProgress.SPECIAL_INPUT_SUCCESS
        self.storage.save_user_session(session)

        # Ask for passenger count
        markup = self.telegram.build_inline_keyboard([
            [("1명", "1"), ("2명", "2"), ("3명", "3")],
            [("4명", "4"), ("5명", "5"), ("6명", "6")],
            [("7명", "7"), ("8명", "8"), ("9명", "9")]
        ])
        self.telegram.send_message(chat_id, Messages.REQUEST_PASSENGER_COUNT, reply_markup=markup)

    def _handle_passenger_count_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle passenger count input."""
        # Validate input with enhanced validator
        is_valid, error = InputValidator.validate_passenger_count(text)

        if not is_valid:
            self.telegram.send_message(chat_id, error)
            return

        count = int(text)

        # Save passenger count
        session.train_info['passengerCount'] = count
        session.last_action = UserProgress.PASSENGER_COUNT_INPUT_SUCCESS
        self.storage.save_user_session(session)

        # Ask for seat strategy if more than 1 passenger
        if count > 1:
            markup = self.telegram.build_inline_keyboard([
                [("🪑 1. 연속 좌석 (권장)", "1")],
                [("🎲 2. 랜덤 분할 배치", "2")]
            ])
            self.telegram.send_message(chat_id, Messages.REQUEST_SEAT_STRATEGY.format(count=count), reply_markup=markup)
        else:
            # Single passenger, skip seat strategy
            session.train_info['seatStrategy'] = 'consecutive'
            session.train_info['seatStrategyShow'] = '단독 좌석'
            session.last_action = UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self._show_final_confirmation(chat_id, session)

    def _handle_seat_strategy_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle seat strategy selection."""
        # Validate with enhanced validator
        is_valid, error = InputValidator.validate_seat_strategy_choice(text)

        if not is_valid:
            markup = self.telegram.build_inline_keyboard([
                [("🪑 1. 연속 좌석 (권장)", "1")],
                [("🎲 2. 랜덤 분할 배치", "2")]
            ])
            self.telegram.send_message(chat_id, error, reply_markup=markup)
            return

        strategy = "consecutive" if text == "1" else "random"
        strategy_display = "연속 좌석" if text == "1" else "랜덤 배치"

        session.train_info['seatStrategy'] = strategy
        session.train_info['seatStrategyShow'] = strategy_display
        session.last_action = UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        self.storage.save_user_session(session)

        self._show_final_confirmation(chat_id, session)

    def _show_final_confirmation(self, chat_id: int, session: UserSession) -> None:
        """Show final confirmation summary."""
        passenger_count = session.train_info.get('passengerCount', 1)
        seat_strategy_display = session.train_info.get('seatStrategyShow', '단독 좌석')

        summary = Messages.CONFIRM_RESERVATION.format(
            depDate=session.train_info.get('depDate', 'N/A'),
            srcLocate=session.train_info.get('srcLocate', 'N/A'),
            dstLocate=session.train_info.get('dstLocate', 'N/A'),
            depTime=session.train_info.get('depTime', '0000')[:4] if session.train_info.get('depTime') else '0000',
            maxDepTime=session.train_info.get('maxDepTime', '2400'),
            trainTypeShow=session.train_info.get('trainTypeShow', 'KTX'),
            specialInfoShow=session.train_info.get('specialInfoShow', 'GENERAL_FIRST'),
            passengerCount=passenger_count,
            seatStrategy=seat_strategy_display
        )
        markup = self.telegram.build_inline_keyboard([
            [("✅ 예약 시작", "Y"), ("❌ 작업 취소", "N")]
        ])
        self.telegram.send_message(chat_id, summary, reply_markup=markup)

    def _handle_final_confirmation(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle final confirmation before starting reservation."""
        is_yes, error = InputValidator.validate_yes_no(text)

        if is_yes is True:
            # Start reservation process
            self._start_reservation(chat_id, session)
        elif is_yes is False:
            session.reset()
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([[("🚀 새 여정 시작 (/start)", "/start")]])
            self.telegram.send_message(chat_id, Messages.CANCELLED_BY_USER, reply_markup=markup)
        else:
            markup = self.telegram.build_inline_keyboard([
                [("✅ 예약 시작", "Y"), ("❌ 작업 취소", "N")]
            ])
            self.telegram.send_message(chat_id, Messages.ERROR_CONFIRM_INVALID, reply_markup=markup)

    def _start_reservation(self, chat_id: int, session: UserSession) -> None:
        """Start the reservation background process."""
        # Create search params
        search_params = TrainSearchParams(
            dep_date=session.train_info['depDate'],
            src_locate=session.train_info['srcLocate'],
            dst_locate=session.train_info['dstLocate'],
            dep_time=session.train_info['depTime'],
            max_dep_time=session.train_info['maxDepTime'],
            train_type=session.train_info['trainType'],
            train_type_display=session.train_info['trainTypeShow'],
            special_option=session.train_info['specialInfo'],
            special_option_display=session.train_info['specialInfoShow'],
            passenger_count=session.train_info.get('passengerCount', 1),
            seat_strategy=session.train_info.get('seatStrategy', 'consecutive')
        )

        # Update session
        session.last_action = UserProgress.FINDING_TICKET
        self.storage.save_user_session(session)

        # Start reservation
        success = self.reservation.start_reservation_process(
            chat_id=chat_id,
            username=session.credentials.korail_id,
            password=session.credentials.korail_pw,
            search_params=search_params
        )

        if not success:
            logger.error(f"Failed to start reservation for chat_id={chat_id}")
            session.reset()
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, Messages.ERROR_RESERVATION_START_FAILED)

    def _handle_running_reservation_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """
        Handle user text input while a reservation is actively running.
        Allows natural language modifications (in-flight editing), status checks, or cancellation.
        """
        cleaned = text.strip()
        logger.info(f"Handling running reservation input for chat_id={chat_id}: '{cleaned}'")

        # 1. Cancellation intent
        cancel_keywords = ["취소", "중단", "그만", "stop", "halt", "그만해", "작업 취소", "중지"]
        if any(kw == cleaned.lower() or cleaned.lower().startswith(kw) for kw in cancel_keywords):
            self.reservation.cancel_reservation(chat_id)
            return

        # 2. Status inquiry intent
        status_keywords = ["상태", "status", "진행상황", "현황", "어디까지", "진행상태"]
        if any(kw in cleaned.lower() for kw in status_keywords):
            status_msg = self.reservation.get_status(chat_id)
            self.telegram.send_message(chat_id, status_msg)
            return

        # 3. Running reservation parameters
        current_res = self.storage.get_running_reservation(chat_id)
        params = current_res.search_params if (current_res and current_res.search_params) else session.search_params

        current_slots = {}
        if params:
            current_slots = {
                "src_locate": params.src_locate,
                "dst_locate": params.dst_locate,
                "dep_date": params.dep_date,
                "dep_time": params.dep_time[:4] if params.dep_time else "0000",
                "max_dep_time": params.max_dep_time if params.max_dep_time else "2400",
                "train_type": "KTX" if "KTX" in str(params.train_type).upper() else "ALL",
                "reserve_option": "SPECIAL_FIRST" if "SPECIAL" in str(params.special_option).upper() else "GENERAL_FIRST",
                "passenger_count": params.passenger_count,
                "seat_strategy": "2" if params.seat_strategy == "random" else "1",
            }
        elif session.train_info:
            info = session.train_info
            current_slots = {
                "src_locate": info.get("srcLocate"),
                "dst_locate": info.get("dstLocate"),
                "dep_date": info.get("depDate"),
                "dep_time": info.get("depTime", "0000")[:4] if info.get("depTime") else "0000",
                "max_dep_time": info.get("maxDepTime", "2400"),
                "train_type": "KTX" if "KTX" in str(info.get("trainType", "")).upper() else "ALL",
                "reserve_option": "SPECIAL_FIRST" if "SPECIAL" in str(info.get("specialInfo", "")).upper() else "GENERAL_FIRST",
                "passenger_count": info.get("passengerCount", 1),
                "seat_strategy": "2" if info.get("seatStrategy") == "random" else "1",
            }

        parsed = self.ai_agent.parse_natural_reservation(cleaned, current_params=dict(current_slots))

        # Check differences
        has_changes = False
        if current_slots:
            for k in ("src_locate", "dst_locate", "dep_date", "dep_time", "max_dep_time"):
                if parsed.get(k) and parsed[k] != current_slots.get(k):
                    has_changes = True
                    break
            if not has_changes and parsed.get("passenger_count") is not None:
                if int(parsed["passenger_count"]) != int(current_slots.get("passenger_count", 1)):
                    has_changes = True
            if not has_changes and parsed.get("seat_strategy") is not None:
                if str(parsed["seat_strategy"]) != str(current_slots.get("seat_strategy", "1")):
                    has_changes = True
            if not has_changes and parsed.get("train_type") is not None:
                if parsed["train_type"] != current_slots.get("train_type"):
                    has_changes = True
            if not has_changes and parsed.get("reserve_option") is not None:
                if parsed["reserve_option"] != current_slots.get("reserve_option"):
                    has_changes = True

        if not parsed.get("is_reservation_intent", True):
            has_changes = False

        if not has_changes:
            self._handle_already_processing(chat_id, session)
            return

        # 4. Sync updated slots to session and restart reservation
        self._sync_nl_slots_to_train_info(session, parsed)

        new_search_params = TrainSearchParams(
            dep_date=session.train_info['depDate'],
            src_locate=session.train_info['srcLocate'],
            dst_locate=session.train_info['dstLocate'],
            dep_time=session.train_info['depTime'],
            max_dep_time=session.train_info['maxDepTime'],
            train_type=session.train_info['trainType'],
            train_type_display=session.train_info['trainTypeShow'],
            special_option=session.train_info['specialInfo'],
            special_option_display=session.train_info['specialInfoShow'],
            passenger_count=session.train_info.get('passengerCount', 1),
            seat_strategy=session.train_info.get('seatStrategy', 'consecutive')
        )

        success = self.reservation.restart_reservation(chat_id, new_search_params)
        if not success:
            logger.error(f"Failed to restart reservation for chat_id={chat_id}")
            self.telegram.send_message(chat_id, "❌ 여정 변경 중 오류가 발생했습니다. 다시 시도해주세요.")

    def _handle_already_processing(self, chat_id: int, session: UserSession) -> None:
        """Handle message when reservation is already in progress."""
        info = session.train_info
        message = Messages.ALREADY_RUNNING.format(
            depDate=info.get('depDate', 'N/A'),
            srcLocate=info.get('srcLocate', 'N/A'),
            dstLocate=info.get('dstLocate', 'N/A'),
            depTime=info.get('depTime', 'N/A')[:4] if info.get('depTime') else 'N/A',
            trainTypeShow=info.get('trainTypeShow', 'N/A'),
            specialInfoShow=info.get('specialInfoShow', 'N/A')
        )
        markup = self.telegram.build_inline_keyboard([
            [("🛑 예약 취소하기", "CANCEL_RESERVATION")]
        ])
        self.telegram.send_message(chat_id, message, reply_markup=markup)

    def _handle_edit_select_field(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle field selection for editing reservation options."""
        choice = text.strip()

        if choice == "0":
            session.last_action = UserProgress.FINDING_TICKET
            session.editing_field = None
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, Messages.EDIT_CANCELLED)
            return

        reservation = self.storage.get_running_reservation(chat_id)
        if not reservation or not reservation.search_params:
            self.telegram.send_message(chat_id, Messages.EDIT_NO_RUNNING_RESERVATION)
            session.reset()
            self.storage.save_user_session(session)
            return

        if choice == "1":
            session.editing_field = "seat_strategy"
            session.last_action = UserProgress.EDIT_INPUT_VALUE
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([
                [("🪑 1. 연속 좌석", "1"), ("🎲 2. 랜덤 배치", "2")],
                [("❌ 수정 취소", "0")]
            ])
            self.telegram.send_message(
                chat_id,
                "🪑 좌석 배치 방식을 선택해주세요:\n\n"
                "1️⃣ 연속 좌석 (붙어있는 좌석만 예약)\n"
                "2️⃣ 랜덤 배치 (한 자리씩 개별 예약)",
                reply_markup=markup
            )
        elif choice == "2":
            session.editing_field = "dep_time"
            session.last_action = UserProgress.EDIT_INPUT_VALUE
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([
                [("❌ 수정 취소", "0")]
            ])
            self.telegram.send_message(
                chat_id,
                "🕐 검색 시간대를 입력해주세요.\n\n"
                "형식: 시작시각-최대시각 (예: 1200-2400 또는 1800-2200)\n"
                "(단일 시각 입력 시 해당 시각 이후 전체 검색)\n\n"
                "취소하려면 아래 취소 버튼을 누르거나 0을 입력하세요.",
                reply_markup=markup
            )
        elif choice == "3":
            session.editing_field = "train_type"
            session.last_action = UserProgress.EDIT_INPUT_VALUE
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([
                [("🚄 1. KTX / KTX-산천만", "1")],
                [("🚅 2. 모든 열차 포함", "2")],
                [("❌ 수정 취소", "0")]
            ])
            self.telegram.send_message(
                chat_id,
                "🚄 열차 종류를 선택해주세요:\n\n"
                "1️⃣ KTX / KTX-산천만\n"
                "2️⃣ 모든 열차 (ITX-새마을, 무궁화 등 포함)",
                reply_markup=markup
            )
        elif choice == "4":
            session.editing_field = "special_option"
            session.last_action = UserProgress.EDIT_INPUT_VALUE
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([
                [("1️⃣ 일반실 우선", "1"), ("2️⃣ 일반실만", "2")],
                [("3️⃣ 특실 우선", "3"), ("4️⃣ 특실만", "4")],
                [("❌ 수정 취소", "0")]
            ])
            self.telegram.send_message(
                chat_id,
                "💺 좌석 종류를 선택해주세요:\n\n"
                "1️⃣ 일반실 우선\n"
                "2️⃣ 일반실만\n"
                "3️⃣ 특실 우선\n"
                "4️⃣ 특실만",
                reply_markup=markup
            )
        elif choice == "5":
            session.editing_field = "passenger_count"
            session.last_action = UserProgress.EDIT_INPUT_VALUE
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([
                [("1명", "1"), ("2명", "2"), ("3명", "3")],
                [("4명", "4"), ("5명", "5"), ("6명", "6")],
                [("7명", "7"), ("8명", "8"), ("9명", "9")],
                [("❌ 수정 취소", "0")]
            ])
            self.telegram.send_message(
                chat_id,
                "👥 탑승 인원수를 선택해주세요 (1~9명):",
                reply_markup=markup
            )
        else:
            markup = self.telegram.build_inline_keyboard([
                [("1️⃣ 좌석 배치 변경", "1"), ("2️⃣ 검색 시간대 변경", "2")],
                [("3️⃣ 열차 종류 변경", "3"), ("4️⃣ 좌석 종류 변경", "4")],
                [("5️⃣ 탑승 인원수 변경", "5"), ("0️⃣ 수정 취소", "0")]
            ])
            self.telegram.send_message(chat_id, Messages.EDIT_INVALID_CHOICE, reply_markup=markup)

    def _handle_edit_input_value(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle new value input for the selected option."""
        value = text.strip()

        if value == "0":
            session.last_action = UserProgress.FINDING_TICKET
            session.editing_field = None
            self.storage.save_user_session(session)
            self.telegram.send_message(chat_id, Messages.EDIT_CANCELLED)
            return

        reservation = self.storage.get_running_reservation(chat_id)
        if not reservation or not reservation.search_params:
            self.telegram.send_message(chat_id, Messages.EDIT_NO_RUNNING_RESERVATION)
            session.reset()
            self.storage.save_user_session(session)
            return

        params = reservation.search_params
        field = session.editing_field

        if field == "seat_strategy":
            if value == "1":
                params.seat_strategy = "consecutive"
            elif value == "2":
                params.seat_strategy = "random"
            else:
                self.telegram.send_message(chat_id, "❌ 1 (연속 좌석) 또는 2 (랜덤 배치)를 입력해주세요.")
                return

        elif field == "dep_time":
            if "-" in value:
                parts = value.split("-", 1)
                start_t, max_t = parts[0].strip(), parts[1].strip()
            else:
                start_t, max_t = value, "2400"

            is_valid, err = InputValidator.validate_time(start_t)
            if not is_valid:
                self.telegram.send_message(chat_id, f"❌ 시작 {err}")
                return

            if max_t == "2400":
                is_valid_max, err_max = True, None
            else:
                is_valid_max, err_max = InputValidator.validate_time(max_t)

            if not is_valid_max:
                self.telegram.send_message(chat_id, f"❌ 종료 {err_max}")
                return

            if max_t != "2400" and int(max_t) < int(start_t):
                self.telegram.send_message(
                    chat_id,
                    f"❌ 검색 종료 시각({max_t})은 시작 시각({start_t})보다 이후여야 합니다.\n"
                    f"다시 입력해주세요. (예: 1200-2400 또는 1800-2200)"
                )
                return

            params.dep_time = f"{start_t}00"
            params.max_dep_time = max_t

        elif field == "train_type":
            is_valid, err = InputValidator.validate_train_type_choice(value)
            if not is_valid:
                self.telegram.send_message(chat_id, err)
                return
            if value == "1":
                params.train_type = "FLAGSHIP"
                params.train_type_display = "Flagship (플래그십)"
            else:
                params.train_type = "ENTRY"
                params.train_type_display = "Entry (엔트리)"

        elif field == "special_option":
            is_valid, err = InputValidator.validate_special_option_choice(value)
            if not is_valid:
                self.telegram.send_message(chat_id, err)
                return
            options = {
                "1": ("ReserveOption.GENERAL_FIRST", "GENERAL_FIRST"),
                "2": ("ReserveOption.GENERAL_ONLY", "GENERAL_ONLY"),
                "3": ("ReserveOption.SPECIAL_FIRST", "SPECIAL_FIRST"),
                "4": ("ReserveOption.SPECIAL_ONLY", "SPECIAL_ONLY")
            }
            params.special_option, params.special_option_display = options[value]

        elif field == "passenger_count":
            is_valid, err = InputValidator.validate_passenger_count(value)
            if not is_valid:
                self.telegram.send_message(chat_id, err)
                return
            params.passenger_count = int(value)

        else:
            session.last_action = UserProgress.FINDING_TICKET
            session.editing_field = None
            self.storage.save_user_session(session)
            return

        # Restart reservation with updated params
        self.reservation.restart_reservation(chat_id, params)

    def _handle_hitl_input(self, chat_id: int, text: str, hitl_request: dict) -> bool:
        """
        Handle user's response to an active HITL decision request.
        Returns True if the message was handled as a HITL choice, False otherwise.
        """
        cleaned = text.strip()
        options = hitl_request.get("options", [])
        valid_ids = [str(opt.get("id")) for opt in options]

        if cleaned in valid_ids:
            chosen_id = int(cleaned)
            chosen_title = next((opt.get("title") for opt in options if opt.get("id") == chosen_id), f"{chosen_id}번")
            self.storage.set_hitl_decision(chat_id, chosen_id)
            self.telegram.send_message(
                chat_id,
                f"✅ [{chosen_title}] 선택되었습니다. Agent-Conductor에 즉시 반영합니다! 🚄"
            )
            return True
        return False

    # ==========================================
    # Natural Language Reservation Flow Handlers
    # ==========================================

    def _handle_natural_reservation_input(self, chat_id: int, text: str, session: UserSession) -> None:
        """Handle natural language reservation request or iterative slot updates."""
        logger.info(f"Processing natural language reservation input for chat_id={chat_id}: '{text}'")

        # Get existing slots if any
        current_slots = session.train_info.get("nl_slots", {})

        # Parse with AI Agent
        parsed = self.ai_agent.parse_natural_reservation(text, current_params=current_slots)

        # Check if no meaningful reservation information could be extracted
        missing = parsed.get("missing_slots", [])
        has_core_info = any([parsed.get("src_locate"), parsed.get("dst_locate"), parsed.get("dep_date")])

        if len(missing) >= 5 and not has_core_info:
            session.last_action = UserProgress.NATURAL_INPUT_CONFIRMATION
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([
                [("오늘", "NL_SET_dep_date:TODAY"), ("내일", "NL_SET_dep_date:TOMORROW"), ("모레", "NL_SET_dep_date:DAY_AFTER")],
                [("서울 ➔ 부산", "NL_SET_ROUTE:서울:부산"), ("부산 ➔ 서울", "NL_SET_ROUTE:부산:서울")],
                [("용산 ➔ 광주송정", "NL_SET_ROUTE:용산:광주송정"), ("동대구 ➔ 서울", "NL_SET_ROUTE:동대구:서울")],
                [("❌ 취소", "NL_CANCEL")]
            ])
            self.telegram.send_message(
                chat_id,
                "🤖 입력하신 내용에서 여정 정보를 찾지 못했습니다.\n\n"
                "다음과 같이 원하는 여정 정보를 편하게 말씀해주세요:\n"
                "👉 '내일 오전 10시 서울에서 부산 KTX 2명'\n"
                "👉 '이번 주 금요일 18시 용산-광주송정'\n\n"
                "또는 기존 방식대로 날짜 8자리(예: 20260920)를 입력하거나 아래 빠른 선택을 이용하세요.",
                reply_markup=markup
            )
            return

        self._render_natural_confirmation_or_slots(chat_id, session, parsed)

    def _render_natural_confirmation_or_slots(self, chat_id: int, session: UserSession, slots: dict) -> None:
        """Render slot-filling questions or the final confirmation card."""
        missing = slots.get("missing_slots", [])

        # Sync slots to session
        self._sync_nl_slots_to_train_info(session, slots)
        session.last_action = UserProgress.NATURAL_INPUT_CONFIRMATION
        self.storage.save_user_session(session)

        if not missing:
            # All required slots are present -> show final confirmation card
            card = MessageTemplates.natural_reservation_summary(slots)
            markup = self.telegram.build_inline_keyboard([
                [("🚀 예약 시작", "NL_START"), ("✏️ 다시 입력", "NL_RESET")],
                [("❌ 작업 취소", "NL_CANCEL")]
            ])
            self.telegram.send_message(chat_id, card, reply_markup=markup)
        else:
            # Some slots are missing -> show slot filling card with quick reply buttons
            prompt_text = MessageTemplates.slot_filling_request(slots, missing)
            markup = self._build_slot_filling_buttons(missing, slots)
            self.telegram.send_message(chat_id, prompt_text, reply_markup=markup)

    def _build_slot_filling_buttons(self, missing_slots: list, slots: dict) -> dict:
        """Build Telegram inline keyboard for missing slots."""
        rows = []

        # If departure date missing
        if "dep_date" in missing_slots:
            rows.append([
                ("오늘", "NL_SET_dep_date:TODAY"),
                ("내일", "NL_SET_dep_date:TOMORROW"),
                ("모레", "NL_SET_dep_date:DAY_AFTER"),
            ])

        # If departure station missing
        if "src_locate" in missing_slots:
            rows.append([
                ("서울", "NL_SET_src_locate:서울"),
                ("용산", "NL_SET_src_locate:용산"),
                ("동대구", "NL_SET_src_locate:동대구"),
                ("부산", "NL_SET_src_locate:부산"),
            ])

        # If destination station missing
        if "dst_locate" in missing_slots:
            rows.append([
                ("부산", "NL_SET_dst_locate:부산"),
                ("동대구", "NL_SET_dst_locate:동대구"),
                ("서울", "NL_SET_dst_locate:서울"),
                ("대전", "NL_SET_dst_locate:대전"),
            ])

        # If departure time missing
        if "dep_time" in missing_slots:
            rows.append([
                ("🌅 아침 (08시~)", "NL_SET_dep_time:0800"),
                ("☀️ 낮 (12시~)", "NL_SET_dep_time:1200"),
            ])
            rows.append([
                ("🌆 저녁 (18시~)", "NL_SET_dep_time:1800"),
                ("⚡ 첫차부터 (00시~)", "NL_SET_dep_time:0000"),
            ])

        # If passenger count missing
        if "passenger_count" in missing_slots:
            rows.append([
                ("1명", "NL_SET_passenger_count:1"),
                ("2명", "NL_SET_passenger_count:2"),
                ("3명", "NL_SET_passenger_count:3"),
                ("4명", "NL_SET_passenger_count:4"),
            ])

        # Quick start with defaults if main routing slots (src, dst, date) exist
        if slots.get("src_locate") and slots.get("dst_locate") and slots.get("dep_date"):
            rows.append([
                ("⚡ 기본값(첫차/1명)으로 바로 시작", "NL_DEFAULT_START")
            ])

        rows.append([
            ("❌ 취소", "NL_CANCEL")
        ])

        return self.telegram.build_inline_keyboard(rows)

    def _sync_nl_slots_to_train_info(self, session: UserSession, slots: dict) -> None:
        """Sync parsed natural language slots to standard session.train_info format."""
        session.train_info['nl_slots'] = slots

        if slots.get('dep_date'):
            session.train_info['depDate'] = slots['dep_date']
        if slots.get('src_locate'):
            session.train_info['srcLocate'] = slots['src_locate']
        if slots.get('dst_locate'):
            session.train_info['dstLocate'] = slots['dst_locate']

        dep_t = slots.get('dep_time', '0000')
        if len(dep_t) == 4:
            session.train_info['depTime'] = dep_t + "00"
        elif len(dep_t) == 6:
            session.train_info['depTime'] = dep_t
        else:
            session.train_info['depTime'] = "000000"

        max_t = slots.get('max_dep_time', '2400')
        session.train_info['maxDepTime'] = max_t if len(max_t) == 4 else '2400'

        train_type = slots.get('train_type', 'FLAGSHIP')
        if train_type in ('FLAGSHIP', 'KTX'):
            session.train_info['trainType'] = "FLAGSHIP"
            session.train_info['trainTypeShow'] = "Flagship (플래그십)"
        else:
            session.train_info['trainType'] = "ENTRY"
            session.train_info['trainTypeShow'] = "Entry (엔트리)"

        reserve_opt = slots.get('reserve_option', 'GENERAL_FIRST')
        option_map = {
            "GENERAL_FIRST": (ReserveOption.GENERAL_FIRST, "GENERAL_FIRST"),
            "GENERAL_ONLY": (ReserveOption.GENERAL_ONLY, "GENERAL_ONLY"),
            "SPECIAL_FIRST": (ReserveOption.SPECIAL_FIRST, "SPECIAL_FIRST"),
            "SPECIAL_ONLY": (ReserveOption.SPECIAL_ONLY, "SPECIAL_ONLY"),
        }
        opt, opt_show = option_map.get(reserve_opt, (ReserveOption.GENERAL_FIRST, "GENERAL_FIRST"))
        session.train_info['specialInfo'] = str(opt)
        session.train_info['specialInfoShow'] = opt_show

        session.train_info['passengerCount'] = int(slots.get('passenger_count', 1))

        seat_strat = str(slots.get('seat_strategy', '1'))
        if seat_strat == '2':
            session.train_info['seatStrategy'] = 'random'
            session.train_info['seatStrategyShow'] = '랜덤 배치'
        else:
            session.train_info['seatStrategy'] = 'consecutive'
            session.train_info['seatStrategyShow'] = '연속 좌석'

    def handle_natural_callback(self, chat_id: int, callback_data: str, session: UserSession) -> None:
        """Handle inline button clicks for natural language reservation flow."""
        logger.info(f"🔘 [Natural Language Callback] chat_id={chat_id}, callback_data='{callback_data}'")

        if callback_data == "NL_CANCEL":
            session.reset()
            self.storage.save_user_session(session)
            markup = self.telegram.build_inline_keyboard([[("🚀 새 여정 시작 (/start)", "/start")]])
            self.telegram.send_message(chat_id, Messages.CANCELLED_BY_USER, reply_markup=markup)
            return

        slots = dict(session.train_info.get("nl_slots", {}))

        if callback_data == "NL_START":
            if not slots.get("missing_slots"):
                self._sync_nl_slots_to_train_info(session, slots)
                self._start_reservation(chat_id, session)
            else:
                self._render_natural_confirmation_or_slots(chat_id, session, slots)
            return

        if callback_data == "NL_RESET":
            session.train_info["nl_slots"] = {}
            session.last_action = UserProgress.PW_INPUT_SUCCESS
            self.storage.save_user_session(session)
            self.telegram.send_message(
                chat_id,
                "🔄 예약 내용을 초기화했습니다.\n예약하고 싶으신 내용을 다시 말씀해주세요.\n예: '내일 오후 2시 서울에서 부산 KTX 1명'"
            )
            return

        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)

        if callback_data == "NL_DEFAULT_START":
            if not slots.get("dep_time"):
                slots["dep_time"] = "0000"
            if not slots.get("passenger_count"):
                slots["passenger_count"] = 1
            if not slots.get("dep_date"):
                slots["dep_date"] = now_kst.strftime("%Y%m%d")

            slots = self.ai_agent._merge_and_validate_slots(slots, slots, "", now_kst)
            self._sync_nl_slots_to_train_info(session, slots)
            self.storage.save_user_session(session)
            self._start_reservation(chat_id, session)
            return

        if callback_data.startswith("NL_SET_ROUTE:"):
            parts = callback_data.split(":")
            if len(parts) == 3:
                slots["src_locate"] = parts[1]
                slots["dst_locate"] = parts[2]
                slots = self.ai_agent._merge_and_validate_slots(slots, slots, "", now_kst)
                self._sync_nl_slots_to_train_info(session, slots)
                self._render_natural_confirmation_or_slots(chat_id, session, slots)
            return

        if callback_data.startswith("NL_SET_"):
            content = callback_data[len("NL_SET_"):]
            if ":" in content:
                field, val = content.split(":", 1)
                if field == "dep_date":
                    if val == "TODAY":
                        slots["dep_date"] = now_kst.strftime("%Y%m%d")
                    elif val == "TOMORROW":
                        slots["dep_date"] = (now_kst + timedelta(days=1)).strftime("%Y%m%d")
                    elif val == "DAY_AFTER":
                        slots["dep_date"] = (now_kst + timedelta(days=2)).strftime("%Y%m%d")
                    else:
                        slots["dep_date"] = val
                elif field == "passenger_count":
                    slots["passenger_count"] = int(val)
                elif field == "dep_time":
                    slots["dep_time"] = val
                elif field in ("src_locate", "dst_locate"):
                    slots[field] = val

                slots = self.ai_agent._merge_and_validate_slots(slots, slots, "", now_kst)
                self._sync_nl_slots_to_train_info(session, slots)
                self._render_natural_confirmation_or_slots(chat_id, session, slots)

