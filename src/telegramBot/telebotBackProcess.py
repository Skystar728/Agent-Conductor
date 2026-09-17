"""
Background process for train reservation.

This module is executed as a subprocess to continuously search for
and attempt to reserve trains. It has been refactored to use the new
service architecture while maintaining backward compatibility.
"""
import sys
import time
import requests
from datetime import datetime, timedelta, timezone
from korail2 import TrainType, ReserveOption

# Add src to path
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(script_dir)
sys.path.insert(0, src_dir)

from config.settings import settings
from storage.redis import RedisStorage
from services import (
    TrainService, KorailService, TelegramService, PaymentReminderService,
    MultiReservationReminderService, AIAgentService
)
from services.korail_service import DuplicateReservationError
from models import MultiReservationStatus, SingleReservationInfo, ReservationPaymentStatus
from utils.logger import get_logger, LoggerFactory

logger = get_logger(__name__)

# Set recursion limit
sys.setrecursionlimit(settings.RECURSION_LIMIT)


class BackgroundReservationProcess:
    """Background process for train reservation."""

    def __init__(self):
        """Initialize from command line arguments."""
        if len(sys.argv) < 11:
            logger.error("Insufficient arguments")
            sys.exit(1)

        self.username = sys.argv[1]
        self.password = sys.argv[2]
        self.dep_date = sys.argv[3]
        self.src_locate = sys.argv[4]
        self.dst_locate = sys.argv[5]
        self.dep_time = sys.argv[6]
        self.train_type_str = sys.argv[7]
        self.special_info_str = sys.argv[8]
        self.chat_id = sys.argv[9]
        self.max_dep_time = sys.argv[10]

        # New parameters with defaults for backward compatibility
        self.passenger_count = int(sys.argv[11]) if len(sys.argv) > 11 else 1
        self.seat_strategy = sys.argv[12] if len(sys.argv) > 12 else "consecutive"

        # Parse train type
        self.train_type = self._parse_train_type(self.train_type_str)
        self.reserve_option = self._parse_reserve_option(self.special_info_str)

        # Initialize services
        self.storage = RedisStorage()
        self.telegram = TelegramService(settings.TELEGRAM_BOT_TOKEN)
        self.payment_reminder = PaymentReminderService(self.storage, self.telegram)
        self.multi_reminder = MultiReservationReminderService(self.storage, self.telegram)
        self.train = TrainService()
        self.korail = self.train  # Backward compatibility
        self.ai_agent = AIAgentService(self.storage)
        self.train.error_listener = self._on_korail_error
        self.train.waf_block_listener = self._on_waf_block
        self.train.maintenance_listener = self._on_maintenance

        # Stage 2 Security: If password was hidden with __REDIS__, load securely from storage
        if not self.password or self.password == "__REDIS__":
            session = self.storage.get_user_session(int(self.chat_id))
            if session and session.credentials and session.credentials.korail_pw:
                self.password = session.credentials.korail_pw
                if not self.username or self.username == "__REDIS__":
                    self.username = session.credentials.korail_id
                logger.info(f"Loaded Korail credentials securely from Redis for user {self.username}")
            else:
                logger.error(f"Failed to load Korail credentials from Redis for chat_id={self.chat_id}")

        logger.info(f"Redis storage connected: {settings.REDIS_HOST}:{settings.REDIS_PORT}")

        # Restore debug mode from Redis
        if self.storage.is_debug_mode():
            LoggerFactory.set_log_level("DEBUG")
            logger.info("Debug mode restored from Redis - log level set to DEBUG")

        logger.info(f"========================================")
        logger.info(f"Background Process Initialized")
        logger.info(f"========================================")
        logger.info(f"  chat_id: {self.chat_id}")
        logger.info(f"  username: {self.username}")
        logger.info(f"  dep_date: '{self.dep_date}'")
        logger.info(f"  src_locate: '{self.src_locate}'")
        logger.info(f"  dst_locate: '{self.dst_locate}'")
        logger.info(f"  dep_time: '{self.dep_time}'")
        logger.info(f"  max_dep_time: '{self.max_dep_time}'")
        logger.info(f"  train_type_str: '{self.train_type_str}' -> {self.train_type}")
        logger.info(f"  special_info_str: '{self.special_info_str}' -> {self.reserve_option}")
        logger.info(f"  passenger_count: {self.passenger_count}")
        logger.info(f"  seat_strategy: '{self.seat_strategy}'")
        logger.info(f"========================================")

    def _parse_train_type(self, train_type_str: str) -> TrainType:
        """Parse train type from string."""
        s = train_type_str.upper()
        if "FLAGSHIP" in s or "KTX" in s or train_type_str == "100":
            return TrainType.KTX
        return TrainType.ALL

    def _parse_reserve_option(self, option_str: str) -> ReserveOption:
        """Parse reserve option from string."""
        s = option_str.upper()
        options = (
            ("GENERAL_FIRST", ReserveOption.GENERAL_FIRST),
            ("GENERAL_ONLY", ReserveOption.GENERAL_ONLY),
            ("SPECIAL_FIRST", ReserveOption.SPECIAL_FIRST),
            ("SPECIAL_ONLY", ReserveOption.SPECIAL_ONLY),
        )
        for option_name, option in options:
            if option_name in s:
                return option
        return ReserveOption.GENERAL_FIRST

    def run(self):
        """Run the reservation process."""
        try:
            logger.info(f"Logging in as {self.username}...")

            # Login
            if not self.korail.login(self.username, self.password):
                logger.error("Login failed")
                message = f"""
❌ 철도 시스템 로그인 실패

아이디/비밀번호가 올바르지 않거나 철도 서버에 문제가 있습니다.

💡 조치 방법:
1. 철도 회원번호를 확인하세요
2. 비밀번호가 올바른지 확인하세요
3. 철도 공식 사이트에서 직접 로그인을 시도해보세요
4. 계정이 잠기지 않았는지 확인하세요

🔗 공식 사이트: {settings.TRAIN_PAYMENT_URL}

정보 수정이 필요하면 /cancel 후 다시 시작하세요.
"""
                self._send_callback(message, status=1)
                return

            logger.info("Login successful, starting reservation loop...")

            # Check seat strategy
            if self.seat_strategy == "random":
                # Random seating: reserve one seat at a time with payment confirmation
                self._run_random_reservation()
                return

            # Consecutive seating: original logic
            # Search and reserve
            reservation = None
            duplicate_notified = False
            while True:
                try:
                    reservation = self.korail.search_and_reserve_loop(
                        dep_date=self.dep_date,
                        src_locate=self.src_locate,
                        dst_locate=self.dst_locate,
                        dep_time=self.dep_time,
                        max_dep_time=self.max_dep_time,
                        train_type=self.train_type,
                        reserve_option=self.reserve_option,
                        passenger_count=self.passenger_count,
                        seat_strategy=self.seat_strategy
                    )
                    break
                except DuplicateReservationError as e:
                    # Duplicate detection - notify user once but continue searching
                    if not duplicate_notified:
                        logger.warning(f"Duplicate reservation detected (first time): {e}")
                        message = f"""
⚠️ 기존 예약 감지

이미 동일한 열차에 대한 예약이 존재합니다.

🔄 기존 예약이 취소될 때까지 대기하면서 계속 검색합니다...

🔗 기존 예약 확인: {settings.TRAIN_PAYMENT_URL}

💡 검색을 중단하려면 /cancel 명령어를 사용하세요.
💡 기존 예약을 취소하면 자동으로 새 예약을 시도합니다.
"""
                        self._send_callback(message, status=2)  # status=2 for warning/info
                        duplicate_notified = True

                    logger.info("Duplicate reservation still active, waiting before retry...")
                    time.sleep(self.korail.get_sleep_interval())
                except requests.exceptions.RequestException as e:
                    logger.error(f"Network error during reservation: {e}")
                    message = f"""
🌐 네트워크 오류

철도 서버와 통신 중 오류가 발생했습니다.

오류 내용: {str(e)}

💡 조치 방법:
1. 인터넷 연결을 확인하세요
2. 잠시 후 다시 시도하세요 (/cancel 후 /start)
3. 철도 서버가 점검 중일 수 있습니다

🔗 공식 사이트 상태 확인: {settings.TRAIN_PAYMENT_URL}
"""
                    self._send_callback(message, status=1)
                    return
                except ValueError as e:
                    logger.error(f"Invalid data during reservation: {e}")
                    message = f"""
⚠️ 입력 데이터 오류

입력하신 정보에 문제가 있습니다.

오류 내용: {str(e)}

💡 조치 방법:
1. 역 이름을 확인하세요 (예: 서울, 부산)
2. 날짜 형식을 확인하세요 (YYYYMMDD)
3. 시간 형식을 확인하세요 (HHMMSS)
4. /cancel 후 정확한 정보로 다시 시도하세요
"""
                    self._send_callback(message, status=1)
                    return
                except Exception as e:
                    # Catch any other unexpected errors from the loop
                    logger.error(f"Unexpected error in reservation loop: {e}", exc_info=True)
                    message = f"""
❌ 예약 검색 중 예상치 못한 오류

오류 유형: {type(e).__name__}
오류 내용: {str(e)}

💡 조치 방법:
1. /cancel 후 다시 시도하세요
2. 문제가 계속되면 관리자에게 문의하세요

로그에 자세한 정보가 기록되었습니다.
"""
                    self._send_callback(message, status=1)
                    return

            if reservation:
                logger.info(f"Reservation successful: {reservation}")

                # Check if this is a random allocation with multiple reservations
                is_random = hasattr(reservation, '_is_random_allocation') and reservation._is_random_allocation
                total_seats = getattr(reservation, '_total_seats', self.passenger_count)

                # Build success message
                payment_guide = f"""⚠️ 중요: {settings.PAYMENT_TIMEOUT_MINUTES}분 내에 결제를 완료하지 않으면 자동 취소됩니다!

📱 [가장 빠른 방법] 스마트폰 공식 승차권 앱
   1. 스마트폰 앱 실행 (자동 로그인)
   2. 하단 [승차권 확인] 또는 [장바구니] 터치
   3. 즉시 [결제하기] 진행

🌐 [웹 브라우저 결제]
   🔗 바로가기: {settings.TRAIN_PAYMENT_URL}
   (로그인 ➡️ 나의 티켓/예약 승차권 ➡️ 결제)

💡 결제 완료 후 아무 메시지나 입력하시면 리마인더 알림이 중단됩니다."""

                if is_random and total_seats > 1:
                    all_reservations = getattr(reservation, '_all_reservations', [reservation])
                    reservation_details = "\n".join([f"좌석 {i+1}: {res}" for i, res in enumerate(all_reservations)])
                    message = f"""
🎉 열차 예약에 성공했습니다!!

총 {total_seats}명의 좌석이 개별적으로 예약되었습니다.
(랜덤 배치 옵션: 좌석이 떨어져 있을 수 있습니다)

예약에 성공한 열차 정보는 다음과 같습니다:
===================
{reservation_details}
===================

{payment_guide}
"""

                    # Create MultiReservationStatus for smart reminders
                    try:
                        self._create_multi_reservation_status(all_reservations, total_seats)
                    except Exception as e:
                        logger.error(f"Failed to create multi-reservation status: {e}", exc_info=True)
                        # Non-critical error - reservation succeeded, just reminder setup failed
                        # Continue with callback

                else:
                    seats_text = f"{self.passenger_count}명" if self.passenger_count > 1 else ""
                    consecutive_text = " (연속된 좌석)" if self.passenger_count > 1 else ""

                    extra_info = []
                    if hasattr(reservation, 'rsv_id') and reservation.rsv_id:
                        extra_info.append(f"🎫 예약번호: {reservation.rsv_id}")
                    if hasattr(reservation, 'price') and reservation.price:
                        extra_info.append(f"💰 결제금액: {reservation.price:,}원")
                    if hasattr(reservation, 'buy_limit_time') and reservation.buy_limit_time:
                        limit_t = str(reservation.buy_limit_time)
                        if len(limit_t) >= 4:
                            extra_info.append(f"⏰ 결제기한: {limit_t[:2]}시 {limit_t[2:4]}분까지")

                    extra_str = ("\n" + "\n".join(extra_info)) if extra_info else ""

                    message = f"""
🎉 열차 예약에 성공했습니다!!

{seats_text}{consecutive_text}

예약에 성공한 열차 정보:
===================
{reservation}{extra_str}
===================

{payment_guide}
"""

                # Send callback with reservation metadata
                self._send_callback(
                    message,
                    status=0,
                    is_multi=is_random and total_seats > 1,
                    total_seats=total_seats,
                    seat_strategy=self.seat_strategy
                )

                # Note: Payment reminders will be started by main app after receiving callback
                # (subprocess and main app don't share memory, so reminders must start in main app)

            else:
                logger.warning("Reservation failed - no result")
                if self.korail.is_waf_max_exceeded():
                    cooldown_min = max(1, int(settings.WAF_COOLDOWN_SECONDS // 60))
                    message = f"""🛑 철도 시스템 방화벽 제한 지속으로 탐색 자동 중단

{cooldown_min}분 쿨다운 후 재시도했으나 여전히 차단이 유지되고 있습니다.
감지 오류: {self.korail.last_waf_error}

추가 요청을 막기 위해 세션에서 로그아웃하고 예약 탐색을 종료했습니다.

💡 조치 방법:
1. 집 공유기 재부팅(또는 MAC 변경)으로 새 공인 IP를 받으시거나
2. 다른 네트워크 환경으로 전환 후 /start로 다시 시도해주세요."""
                    self._send_callback(message, status=1)
                else:
                    message = """
알수 없는 오류로 예약에 실패했습니다. 처음부터 다시 시도해주세요.

[문제가 없는데 계속 반복되는 경우, 이미 해당 열차가 예약이 되었을 수 있습니다. 사이트를 확인해주세요.]
"""
                    self._send_callback(message, status=1)

        except Exception as e:
            logger.error(f"Error in reservation process: {e}", exc_info=True)

            # Build detailed error message
            error_type = type(e).__name__
            error_msg = str(e)

            message = f"""
❌ 예약 프로세스 오류 발생

오류 유형: {error_type}
오류 내용: {error_msg}

📋 상황:
- 출발일: {self.dep_date}
- 출발역: {self.src_locate}
- 도착역: {self.dst_locate}
- 출발시각: {self.dep_time}

💡 조치 방법:
1. 인터넷 연결 상태를 확인하세요
2. 철도 계정 정보가 올바른지 확인하세요
3. 철도 공식 사이트가 정상 작동하는지 확인하세요
4. /cancel 후 다시 시도하세요

🔗 공식 사이트 확인: {settings.TRAIN_PAYMENT_URL}
"""
            self._send_callback(message, status=1)

        logger.info(f"Reservation process ended for {self.username}")

    def _update_multi_reservation_status(self, seat_index: int, reservation, total_seats: int) -> None:
        """
        Create or update MultiReservationStatus for tracking individual seat payment.

        Called after each seat is reserved in random allocation mode.

        Args:
            seat_index: Index of the seat just reserved (0-based)
            reservation: Reservation object from korail2
            total_seats: Total number of seats being reserved
        """
        try:
            now = datetime.now()
            expires_at = now + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES)

            # Get existing status or create new
            multi_status = self.storage.get_multi_reservation_status(self.chat_id)

            if not multi_status or seat_index == 0:
                # First seat - delete any old status and create fresh one
                if seat_index == 0 and multi_status:
                    logger.info(f"Deleting old MultiReservationStatus for chat_id={self.chat_id}")
                    self.storage.delete_multi_reservation_status(self.chat_id)

                logger.info(f"Creating new MultiReservationStatus for chat_id={self.chat_id}")
                multi_status = MultiReservationStatus(
                    chat_id=int(self.chat_id),
                    reservations=[],
                    total_seats=total_seats,
                    seat_strategy=self.seat_strategy,
                    created_at=now,
                    manually_stopped=False
                )

            # Add this reservation
            rsv_id = getattr(reservation, 'rsv_id', f"seat_{seat_index + 1}")
            info = SingleReservationInfo(
                reservation_id=rsv_id,
                reservation_obj=reservation,
                reserved_at=now,
                expires_at=expires_at,
                status=ReservationPaymentStatus.PENDING,
                seat_number=seat_index + 1,
                train_info=str(reservation)
            )
            multi_status.reservations.append(info)

            # Save to storage
            self.storage.save_multi_reservation_status(multi_status)
            logger.info(
                f"Updated MultiReservationStatus: {len(multi_status.reservations)}/{total_seats} seats"
            )

        except Exception as e:
            logger.error(f"Failed to update MultiReservationStatus: {e}", exc_info=True)

    def _create_multi_reservation_status(self, all_reservations: list, total_seats: int) -> None:
        """
        Create MultiReservationStatus for tracking individual seat payment.
        (Legacy method - kept for compatibility)

        Args:
            all_reservations: List of reservation objects from korail2
            total_seats: Total number of seats reserved
        """
        try:
            now = datetime.now()
            expires_at = now + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES)

            # Create SingleReservationInfo for each reservation
            reservation_infos = []
            for i, res in enumerate(all_reservations):
                # Extract reservation ID (try to get from korail2 object)
                rsv_id = getattr(res, 'rsv_id', f"unknown_{i+1}")

                info = SingleReservationInfo(
                    reservation_id=rsv_id,
                    reservation_obj=res,
                    reserved_at=now,
                    expires_at=expires_at,
                    status=ReservationPaymentStatus.PENDING,
                    seat_number=i + 1,
                    train_info=str(res)
                )
                reservation_infos.append(info)

            # Create MultiReservationStatus
            multi_status = MultiReservationStatus(
                chat_id=int(self.chat_id),
                reservations=reservation_infos,
                total_seats=total_seats,
                seat_strategy=self.seat_strategy,
                created_at=now,
                manually_stopped=False
            )

            # Save to storage
            self.storage.save_multi_reservation_status(multi_status)
            logger.info(
                f"Created MultiReservationStatus for chat_id={self.chat_id} "
                f"with {len(reservation_infos)} reservations"
            )

        except Exception as e:
            logger.error(f"Failed to create MultiReservationStatus: {e}", exc_info=True)

    def _send_callback(self, message: str, status: int = 0, is_multi: bool = False,
                       total_seats: int = 1, seat_strategy: str = "consecutive"):
        """
        Send callback to main app.

        Args:
            message: Message to send to user
            status: 0 for success/completion, 1 for error
            is_multi: True if multi-reservation (random allocation with multiple seats)
            total_seats: Total number of seats reserved
            seat_strategy: Seat allocation strategy used
        """
        try:
            callback_url = f"{settings.CALLBACK_BASE_URL}/telebot"
            params = {
                "chatId": self.chat_id,
                "msg": message,
                "status": status,
                "isMulti": "1" if is_multi else "0",
                "totalSeats": str(total_seats),
                "seatStrategy": seat_strategy
            }

            session = requests.session()
            response = session.get(callback_url, params=params, verify=False, timeout=10)

            if response.status_code == 200:
                logger.debug(f"Callback sent successfully: status={status}, is_multi={is_multi}")
            else:
                logger.warning(
                    f"Callback returned non-200 status: {response.status_code}, "
                    f"response={response.text[:200]}"
                )

        except requests.exceptions.Timeout:
            logger.error(f"Callback timeout - main app may be down or slow")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Failed to connect to main app for callback: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending callback: {e}", exc_info=True)

    def _run_random_reservation(self):
        """
        Run random seating reservation: one seat at a time with payment confirmation.

        Flow for each seat:
        1. Search and reserve one seat
        2. Send notification to user
        3. Wait for payment confirmation (up to 10 minutes)
        4. Proceed to next seat
        """
        total_seats = self.passenger_count
        logger.info(f"=== RANDOM SEATING MODE: {total_seats} seats ===")

        for seat_index in range(total_seats):
            logger.info(f"━━━ Seat {seat_index + 1}/{total_seats} ━━━")

            # Reserve one seat (don't set current_seat_index yet!)
            try:
                reservation = self._reserve_single_seat_random(seat_index)
            except Exception as e:
                logger.error(f"Failed to reserve seat {seat_index + 1}: {e}", exc_info=True)
                error_msg = f"""
❌ {seat_index + 1}번째 좌석 예약 실패

오류: {str(e)}

💡 /cancel 후 다시 시도하세요.
"""
                self._send_callback(error_msg, status=1)
                return

            if not reservation:
                if self.korail.is_waf_max_exceeded():
                    cooldown_min = max(1, int(settings.WAF_COOLDOWN_SECONDS // 60))
                    error_msg = f"""🛑 철도 시스템 방화벽 제한 지속으로 탐색 자동 중단

{cooldown_min}분 쿨다운 후 재시도했으나 여전히 차단이 유지되고 있습니다.
감지 오류: {self.korail.last_waf_error}

추가 요청을 막기 위해 세션에서 로그아웃하고 예약 탐색을 종료했습니다.

💡 조치 방법:
1. 집 공유기 재부팅(또는 MAC 변경)으로 새 공인 IP를 받으시거나
2. 다른 네트워크 환경으로 전환 후 /start로 다시 시도해주세요."""
                    self._send_callback(error_msg, status=1)
                    return
                else:
                    logger.error(f"No reservation returned for seat {seat_index + 1}")
                    error_msg = f"❌ {seat_index + 1}번째 좌석 예약 실패 (결과 없음)"
                    self._send_callback(error_msg, status=1)
                    return

            # Save partial reservation
            reservation_data = {
                "seat_index": seat_index,
                "train_info": str(reservation),
                "reserved_at": datetime.now().isoformat()
            }
            self.storage.save_partial_reservation(self.chat_id, seat_index, reservation_data)
            logger.info(f"✅ Seat {seat_index + 1} reserved and saved to Redis")

            # Create or update MultiReservationStatus for reminder service
            self._update_multi_reservation_status(seat_index, reservation, total_seats)

            # NOTE: Don't start reminder service here (subprocess doesn't share memory with main app)
            # Reminder will be started by main app when it receives the callback (status=2)

            # NOW set current seat index for payment waiting
            # This prevents "결제 대기중" message before reservation succeeds
            self.storage.set_current_seat_index(self.chat_id, seat_index)

            # Send notification to user
            message = self._build_partial_reservation_message(
                seat_index,
                total_seats,
                reservation
            )
            self._send_callback(message, status=2, seat_strategy=self.seat_strategy)  # status=2: partial success

            # Wait for payment confirmation (or timeout)
            if seat_index < total_seats - 1:  # Not the last seat
                logger.info(f"⏳ Waiting for payment confirmation for seat {seat_index + 1}...")
                payment_confirmed = self.storage.wait_for_payment(
                    self.chat_id,
                    seat_index,
                    timeout=600  # 10 minutes
                )

                # Always clear current seat index when waiting ends
                self.storage.set_current_seat_index(self.chat_id, None)

                if payment_confirmed:
                    self.multi_reminder.mark_seat_paid(int(self.chat_id), seat_index + 1)
                    logger.info(f"✅ Payment confirmed for seat {seat_index + 1}")
                    # Confirmation message already sent immediately to user by webhook handler
                else:
                    logger.warning(f"⏱ Payment timeout for seat {seat_index + 1}")
                    timeout_msg = f"""
⏱ {seat_index + 1}번째 좌석 결제 시간 초과

10분이 지났습니다. 다음 좌석 예약을 진행합니다.

⚠️ 미결제 좌석은 자동 취소될 수 있으니 빠르게 결제해주세요!
"""
                    self.telegram.send_message(self.chat_id, timeout_msg)

                # Brief pause before next reservation
                logger.info("Waiting 3 seconds before next reservation...")
                time.sleep(3)

        # All seats reserved!
        self.storage.set_current_seat_index(self.chat_id, None)  # Clear index
        all_reservations = self.storage.get_partial_reservations(self.chat_id)

        final_message = self._build_final_random_message(all_reservations, total_seats)
        self._send_callback(final_message, status=0, seat_strategy=self.seat_strategy)  # status=0: complete success

        logger.info(f"🎉 All {total_seats} seats reserved successfully!")

    def _reserve_single_seat_random(self, seat_index: int):
        """
        Reserve a single seat for random allocation.

        Args:
            seat_index: Index of the seat being reserved (0-based)

        Returns:
            Reservation object if successful

        Raises:
            Exception: If reservation fails with non-duplicate error
        """
        logger.info(f"🔍 Starting search for seat {seat_index + 1}...")

        attempts = 0
        max_attempts = None  # Infinite
        duplicate_notified = False  # Track if we already notified about duplicate
        last_heartbeat_time = time.time()

        while True:
            attempts += 1
            now = time.time()
            if now - last_heartbeat_time >= 60.0:
                logger.info(
                    f"🔄 [{self.username}] {seat_index + 1}/{self.passenger_count}번째 좌석 탐색 진행 중... "
                    f"(총 {attempts}회 시도 완료, 취소표 대기 중 | {self.src_locate}→{self.dst_locate})"
                )
                last_heartbeat_time = now

            is_summary = (attempts % 60 == 0)

            if is_summary:
                logger.debug(f"🔄 Search attempt #{attempts} for seat {seat_index + 1}")

            # Search for trains (single passenger)
            try:
                trains = self.korail.search_trains(
                    dep_date=self.dep_date,
                    src_locate=self.src_locate,
                    dst_locate=self.dst_locate,
                    dep_time=self.dep_time,
                    max_dep_time=self.max_dep_time,
                    train_type=self.train_type,
                    passenger_count=1,  # Single seat
                    verbose=is_summary
                )
                if trains:
                    logger.debug(f"✅ Search completed: found {len(trains)} trains")
                elif is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
            except Exception as e:
                logger.error(f"❌ Search failed (attempt #{attempts}): {e}", exc_info=True)
                time.sleep(self.korail.get_sleep_interval())
                continue

            if not trains:
                if self.korail.is_waf_max_exceeded():
                    logger.warning("🛑 Random single seat search stopping due to persistent WAF block")
                    return None
                time.sleep(self.korail.get_sleep_interval())
                continue

            # Try to reserve (trains found = rare, always log)
            duplicate_found = False
            for idx, train in enumerate(trains, 1):
                logger.info(f"🚂 Trying train {idx}/{len(trains)}: {train}")

                reservation = self.korail.reserve_train(
                    train,
                    option=self.reserve_option,
                    passenger_count=1
                )

                if reservation == "DUPLICATE":
                    # Duplicate reservation exists - notify user once and keep retrying
                    logger.warning(f"⚠️ Duplicate reservation detected for seat {seat_index + 1}")
                    duplicate_found = True

                    if not duplicate_notified:
                        # Send notification only once
                        self.telegram.send_message(
                            self.chat_id,
                            f"⚠️ {seat_index + 1}번째 좌석 예약 시도 중 기존 예약 감지\n\n"
                            f"이미 해당 시간에 예약된 좌석이 있습니다.\n"
                            f"기존 예약이 취소될 때까지 10초마다 재시도합니다.\n\n"
                            f"🔗 기존 예약 확인: {settings.KORAIL_PAYMENT_URL}\n\n"
                            f"💡 검색을 중단하려면 /cancel 명령어를 사용하세요.\n"
                            f"💡 기존 예약을 취소하면 자동으로 새 예약을 시도합니다."
                        )
                        duplicate_notified = True
                        logger.info(f"📢 Duplicate notification sent for seat {seat_index + 1}")

                    # Continue to next train
                    continue

                elif reservation:
                    logger.info(f"✅ Seat {seat_index + 1} reserved after {attempts} search attempts!")
                    logger.info(f"🎉 Successfully reserved: {reservation}")
                    return reservation
                else:
                    logger.info(f"  ❌ Train {idx} failed (sold out or unavailable)")

            # All trains in this search failed
            if duplicate_found:
                logger.info(f"⚠️ Duplicate reservation detected, waiting 10s before retry...")
                time.sleep(10)  # Wait 10 seconds when duplicate found
            else:
                logger.debug(f"All {len(trains)} trains sold out in attempt #{attempts}")
                time.sleep(self.korail.get_sleep_interval())

    def _build_partial_reservation_message(self, seat_index: int, total_seats: int, reservation) -> str:
        """Build message for partial reservation success."""
        extra_info = []
        if hasattr(reservation, 'rsv_id') and reservation.rsv_id:
            extra_info.append(f"🎫 예약번호: {reservation.rsv_id}")
        if hasattr(reservation, 'price') and reservation.price:
            extra_info.append(f"💰 결제금액: {reservation.price:,}원")
        if hasattr(reservation, 'buy_limit_time') and reservation.buy_limit_time:
            limit_t = str(reservation.buy_limit_time)
            if len(limit_t) >= 4:
                extra_info.append(f"⏰ 결제기한: {limit_t[:2]}시 {limit_t[2:4]}분까지")

        extra_str = ("\n" + "\n".join(extra_info)) if extra_info else ""

        return f"""
🎉 {seat_index + 1}/{total_seats}번째 좌석 예약 성공!

━━━━━━━━━━━━━━━━━━━━
{reservation}{extra_str}
━━━━━━━━━━━━━━━━━━━━

⚠️ {settings.PAYMENT_TIMEOUT_MINUTES}분 내에 결제를 완료하지 않으면 자동 취소됩니다!

📱 [가장 빠른 방법] 스마트폰 공식 승차권 앱
   1. 스마트폰 앱 실행 (자동 로그인)
   2. 하단 [승차권 확인] 또는 [장바구니] 터치
   3. [결제하기] 진행

🌐 [웹 브라우저 결제]
   🔗 바로가기: {settings.TRAIN_PAYMENT_URL}

💡 결제 후 아무 메시지나 보내면 다음 좌석 예약이 즉시 시작됩니다.
(10분 동안 메시지가 없어도 자동으로 다음 좌석 예약을 진행합니다)
"""

    def _build_final_random_message(self, all_reservations: list, total_seats: int) -> str:
        """Build final message for all random reservations complete."""
        reservation_details = "\n".join([
            f"좌석 {i+1}: {r.get('train_info', 'N/A')}"
            for i, r in enumerate(all_reservations)
        ])

        return f"""
🎉🎉 모든 좌석 예약 완료! 🎉🎉

총 {total_seats}명의 좌석이 개별적으로 예약되었습니다.
(랜덤 배치: 좌석이 떨어져 있을 수 있습니다)

━━━━━━━━━━━━━━━━━━━━
{reservation_details}
━━━━━━━━━━━━━━━━━━━━

⚠️ 중요 안내:
• 모든 좌석을 {settings.PAYMENT_TIMEOUT_MINUTES}분 내 결제해야 합니다! (미결제 시 자동 취소)

📱 [가장 빠른 방법] 스마트폰 공식 승차권 앱
   1. 스마트폰 앱 실행 ➡️ 하단 [승차권 확인/장바구니]
   2. 각 좌석 [결제하기] 진행

🌐 [웹 브라우저 결제]
   🔗 바로가기: {settings.TRAIN_PAYMENT_URL}

✅ 축하합니다! 🎊
"""

    def _on_waf_block(self, err_str: str, block_count: int) -> None:
        """Handle WAF block detection by notifying user and entering cooldown."""
        cooldown_min = max(1, int(settings.WAF_COOLDOWN_SECONDS // 60))
        if block_count == 1:
            msg = f"""⚠️ 철도 모바일 방화벽 제한 감지 (Agent-Conductor 서킷 브레이커)

철도 보안 시스템에 의해 일시적인 요청 제한(403)이 발생했습니다.
IP 차단 연장을 방지하기 위해 {cooldown_min}분 동안 탐색을 일시 중단(쿨다운)합니다.

⏱ {cooldown_min}분 후 1회 안전하게 재시도합니다.
💡 즉시 취소하시려면 /cancel을 입력하세요."""
            self._send_callback(msg, status=2)

    def _on_maintenance(self, is_active: bool, message: str) -> None:
        """Handle Korail server maintenance events."""
        try:
            if is_active:
                min_interval_min = int(settings.MAINTENANCE_CHECK_MIN_INTERVAL // 60)
                max_interval_min = int(settings.MAINTENANCE_CHECK_MAX_INTERVAL // 60)
                msg = f"""ℹ️ 철도 서버 정기 점검 감지

철도 서버 정기 점검이 시작되었습니다. ({message})
점검 시간 동안에는 공식 앱/웹 및 모든 예약 서비스가 일시 중단됩니다.

🔄 시스템이 {min_interval_min}~{max_interval_min}분 간격으로 점검 종료 여부를 확인하며 안전하게 대기합니다.
🚀 점검이 끝나는 즉시 자동으로 예약 탐색을 재개합니다!

💡 대기를 원하지 않으시면 언제든 취소하시거나 /cancel을 입력하세요."""
                self._send_callback(msg, status=2)
            else:
                msg = """🚀 철도 서버 점검 종료 확인!

철도 서버 점검이 완료되어 시스템이 정상 복구되었습니다.
중단되었던 Agent-Conductor 여정 탐색을 즉시 자동으로 재개합니다!"""
                self._send_callback(msg, status=2)
        except Exception as e:
            logger.error(f"Failed to handle maintenance event: {e}")

    def _on_korail_error(self, err_str: str) -> None:
        """Handle Korail error using AI Agent and HITL flow."""
        try:
            plan = self.ai_agent.analyze_error(err_str)
            chat_id_int = int(self.chat_id)

            if plan.requires_hitl and plan.hitl_options:
                # Check if we already asked the user about this error recently (within 30 mins)
                if not self.storage.is_user_notified(chat_id_int, f"hitl:{plan.error_hash}"):
                    self.storage.mark_user_notified(chat_id_int, f"hitl:{plan.error_hash}", ttl=1800)

                    # Format options
                    opt_lines = []
                    for opt in plan.hitl_options:
                        num_emoji = f"{opt['id']}️⃣" if 1 <= opt['id'] <= 9 else f"[{opt['id']}]"
                        opt_lines.append(f"{num_emoji} {opt['title']}")
                    options_text = "\n".join(opt_lines)

                    question = plan.hitl_question or "철도 시스템에서 특이 상황이 감지되었습니다. 어떻게 진행할까요?"
                    timeout_min = max(1, plan.hitl_timeout_seconds // 60)

                    msg = f"""🤖 [AI 비서 상황 보고 & 선택 요청]

{plan.reason}

📋 {question}

━━━━━━━━━━━━━━━━━━━━
{options_text}
━━━━━━━━━━━━━━━━━━━━

💡 아래 버튼을 터치하거나 번호를 입력해주세요.
⏱ {timeout_min}분 내 응답이 없으면 {plan.hitl_default_option}번으로 자동 진행합니다."""

                    hitl_data = {
                        "error_hash": plan.error_hash,
                        "question": question,
                        "options": plan.hitl_options,
                        "default_option": plan.hitl_default_option
                    }
                    self.storage.set_hitl_request(chat_id_int, hitl_data, ttl=plan.hitl_timeout_seconds)

                    # Build inline keyboard buttons
                    button_rows = [
                        [(f"{opt['id']}️⃣ {opt['title']}", str(opt["id"]))]
                        for opt in plan.hitl_options
                    ]
                    markup = self.telegram.build_inline_keyboard(button_rows)
                    self.telegram.send_message(chat_id_int, msg, reply_markup=markup)

                    # Wait for user's decision
                    chosen_id = self.storage.wait_for_hitl_decision(
                        chat_id_int,
                        timeout=plan.hitl_timeout_seconds,
                        default_option=plan.hitl_default_option
                    )

                    # Execute chosen action
                    chosen_opt = next((o for o in plan.hitl_options if o["id"] == chosen_id), None)
                    if chosen_opt:
                        action = chosen_opt.get("action")
                        params = chosen_opt.get("params", {})
                        logger.info(f"Executing HITL choice: {chosen_opt.get('title')} ({action})")
                        if action == "SET_INTERVAL":
                            self.korail.set_search_interval(params.get("seconds", 60.0))
                        elif action == "STOP":
                            self.telegram.send_message(chat_id_int, "🚫 사용자의 요청으로 예약을 안전하게 중단합니다.")
                            try:
                                self.storage.delete_running_reservation(chat_id_int)
                                session = self.storage.get_user_session(chat_id_int)
                                if session:
                                    session.reset()
                                    self.storage.save_user_session(session)
                            except Exception as cleanup_err:
                                logger.warning(f"Error cleaning up on HITL STOP: {cleanup_err}")
                            sys.exit(0)
                        elif action == "SLEEP_UNTIL":
                            sleep_secs = self._calculate_sleep_seconds(params)
                            time.sleep(sleep_secs)
            else:
                # Autonomous mode
                # Notify user only once per hour for this error pattern
                if not self.storage.is_user_notified(chat_id_int, f"notice:{plan.error_hash}"):
                    self.storage.mark_user_notified(chat_id_int, f"notice:{plan.error_hash}", ttl=3600)
                    notice_msg = f"""🤖 [AI 비서 상황 보고]

{plan.user_notice}"""
                    self.telegram.send_message(chat_id_int, notice_msg)

                # Apply autonomous action
                if plan.autonomous_action == "SET_INTERVAL":
                    self.korail.set_search_interval(plan.autonomous_params.get("seconds", 60.0))
                elif plan.autonomous_action == "SLEEP_UNTIL":
                    sleep_secs = self._calculate_sleep_seconds(plan.autonomous_params)
                    time.sleep(sleep_secs)
                elif plan.autonomous_action == "STOP":
                    try:
                        self.storage.delete_running_reservation(chat_id_int)
                        session = self.storage.get_user_session(chat_id_int)
                        if session:
                            session.reset()
                            self.storage.save_user_session(session)
                    except Exception as cleanup_err:
                        logger.warning(f"Error cleaning up on autonomous STOP: {cleanup_err}")
                    sys.exit(0)

        except Exception as e:
            logger.error(f"Error in _on_korail_error: {e}", exc_info=True)

    def _calculate_sleep_seconds(self, params: dict) -> float:
        """Calculate sleep seconds until a specific target time (HH:MM KST) or seconds count."""
        if "seconds" in params:
            try:
                return max(1.0, float(params["seconds"]))
            except (ValueError, TypeError):
                pass

        target_time_str = params.get("target_time")
        if target_time_str:
            try:
                parts = [int(p) for p in str(target_time_str).strip().split(":")]
                target_hour = parts[0]
                target_min = parts[1] if len(parts) > 1 else 0
                target_sec = parts[2] if len(parts) > 2 else 0

                kst = timezone(timedelta(hours=9))
                now_kst = datetime.now(kst)
                target_dt = now_kst.replace(hour=target_hour, minute=target_min, second=target_sec, microsecond=0)

                # If target time is already past today, assume tomorrow
                if target_dt <= now_kst:
                    target_dt += timedelta(days=1)

                diff_seconds = (target_dt - now_kst).total_seconds()
                logger.info(
                    f"🕒 [SLEEP_UNTIL] Target wake time: {target_dt.strftime('%Y-%m-%d %H:%M:%S')} KST "
                    f"(sleeping {int(diff_seconds)} seconds / {diff_seconds/60:.1f} minutes)..."
                )
                return max(5.0, diff_seconds)
            except Exception as e:
                logger.error(f"Failed to parse target_time '{target_time_str}': {e}")

        return 60.0


if __name__ == "__main__":
    process = BackgroundReservationProcess()
    process.run()
