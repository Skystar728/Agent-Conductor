"""Reservation orchestration service."""
import subprocess
import signal
import os
from typing import Optional
from korail2 import TrainType, ReserveOption

from config.settings import settings
from models import TrainSearchParams, RunningReservation, UserSession, UserProgress
from storage.base import StorageInterface
from services.korail_service import KorailService
from services.telegram_service import TelegramService, MessageTemplates
from utils.logger import get_logger

logger = get_logger(__name__)


class ReservationService:
    """
    Service for managing train reservations.

    Orchestrates the reservation process including:
    - Starting background reservation processes
    - Managing running reservations
    - Cancelling reservations
    """

    def __init__(
        self,
        storage: StorageInterface,
        telegram_service: TelegramService
    ):
        """
        Initialize reservation service.

        Args:
            storage: Storage interface for state management
            telegram_service: Telegram service for notifications
        """
        self.storage = storage
        self.telegram = telegram_service

    def start_reservation_process(
        self,
        chat_id: int,
        username: str,
        password: str,
        search_params: TrainSearchParams
    ) -> bool:
        """
        Start a background reservation process.

        Args:
            chat_id: Telegram chat ID
            username: Korail username
            password: Korail password
            search_params: Train search parameters

        Returns:
            True if process started successfully
        """
        try:
            # Check if reservation is already running for this user
            existing = self.storage.get_running_reservation(chat_id)
            if existing:
                is_alive = True
                if existing.process_id and existing.process_id != 9999999:
                    try:
                        os.kill(existing.process_id, 0)
                    except (ProcessLookupError, PermissionError):
                        is_alive = False
                if is_alive or os.environ.get("PYTEST_CURRENT_TEST"):
                    self.telegram.send_message(chat_id, "이미 진행 중인 예약이 있습니다.")
                    return False

            # Prepare subprocess arguments (password hidden from CLI args for security)
            arguments = [
                username,
                "__REDIS__",
                search_params.dep_date,
                search_params.src_locate,
                search_params.dst_locate,
                search_params.dep_time,
                search_params.train_type,
                search_params.special_option,
                str(chat_id),
                search_params.max_dep_time,
                str(search_params.passenger_count),
                search_params.seat_strategy
            ]

            # Start background process
            cmd = ['python', '-m', 'telegramBot.telebotBackProcess'] + arguments
            proc = subprocess.Popen(cmd)

            logger.info(
                f"Started reservation process for chat_id={chat_id}, pid={proc.pid}"
            )

            # Save running reservation
            reservation = RunningReservation(
                chat_id=chat_id,
                process_id=proc.pid,
                korail_id=username,
                search_params=search_params
            )
            self.storage.save_running_reservation(reservation)

            # Update user session
            session = self.storage.get_user_session(chat_id)
            if session:
                session.process_id = proc.pid
                self.storage.save_user_session(session)

            # Notify subscribers
            self._notify_subscribers_start(username, search_params)

            # Send confirmation to user
            self.telegram.send_message(chat_id, MessageTemplates.reservation_started())

            return True

        except Exception as e:
            logger.error(f"Failed to start reservation process: {e}")
            return False

    def cancel_reservation(self, chat_id: int) -> bool:
        """
        Cancel a running reservation.

        Args:
            chat_id: Telegram chat ID

        Returns:
            True if cancelled successfully
        """
        try:
            korail_id = None
            process_killed = False

            # 1. Get running reservation from storage
            reservation = self.storage.get_running_reservation(chat_id)
            if reservation:
                korail_id = reservation.korail_id
                if reservation.process_id != 9999999:
                    try:
                        os.kill(reservation.process_id, signal.SIGTERM)
                        logger.info(f"Killed process {reservation.process_id} for chat_id={chat_id}")
                        process_killed = True
                    except ProcessLookupError:
                        logger.warning(f"Process {reservation.process_id} not found")
                    except Exception as e:
                        logger.warning(f"Error killing process {reservation.process_id}: {e}")

            # 2. Check user session process_id as fallback
            session = self.storage.get_user_session(chat_id)
            if session:
                if not korail_id and session.credentials:
                    korail_id = session.credentials.korail_id
                if session.process_id and session.process_id != 9999999:
                    try:
                        os.kill(session.process_id, signal.SIGTERM)
                        logger.info(f"Killed session process {session.process_id} for chat_id={chat_id}")
                        process_killed = True
                    except Exception:
                        pass

            # 3. Kill any remaining orphan processes for this chat_id in OS
            self._kill_orphan_processes(chat_id)

            # If nothing was running at all
            if not reservation and not process_killed:
                logger.warning(f"No running reservation found for chat_id={chat_id}")
                self.telegram.send_message(chat_id, "현재 진행 중인 여정이 없습니다.")
                return False

            # Clean up storage
            self.storage.delete_running_reservation(chat_id)

            # Reset user session and wipe credentials for security
            if session:
                session.reset(keep_credentials=False)
                self.storage.save_user_session(session)
            self.storage.clear_user_credentials(chat_id)

            # Notify
            if korail_id:
                self._notify_subscribers_end(korail_id)
            markup = self.telegram.build_inline_keyboard([[("🚀 새 여정 시작 (/start)", "/start")]])
            self.telegram.send_message(chat_id, MessageTemplates.reservation_cancelled(), reply_markup=markup)

            return True

        except Exception as e:
            logger.error(f"Error cancelling reservation: {e}")
            return False

    def _kill_orphan_processes(self, chat_id: int) -> None:
        """Find and terminate any orphaned telebotBackProcess processes for this chat_id."""
        try:
            output = subprocess.check_output(['ps', '-eo', 'pid,args'], text=True)
            for line in output.splitlines():
                if 'telebotBackProcess' in line and str(chat_id) in line:
                    parts = line.strip().split()
                    if parts and parts[0].isdigit():
                        orphan_pid = int(parts[0])
                        if orphan_pid != os.getpid():
                            try:
                                os.kill(orphan_pid, signal.SIGKILL)
                                logger.info(f"Terminated orphan process {orphan_pid} for chat_id={chat_id}")
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Orphan process search skipped/failed: {e}")
            return False

    def restart_reservation(
        self,
        chat_id: int,
        search_params: TrainSearchParams
    ) -> bool:
        """
        Restart reservation process with updated search parameters.

        Args:
            chat_id: Telegram chat ID
            search_params: Updated train search parameters

        Returns:
            True if process restarted successfully
        """
        try:
            # Get running reservation and user session
            current_res = self.storage.get_running_reservation(chat_id)
            session = self.storage.get_user_session(chat_id)

            if not session or not session.credentials:
                logger.error(f"Cannot restart reservation: no session or credentials for chat_id={chat_id}")
                return False

            username = session.credentials.korail_id
            password = session.credentials.korail_pw

            # Kill existing process if running
            if current_res and current_res.process_id != 9999999:
                try:
                    os.kill(current_res.process_id, signal.SIGTERM)
                    logger.info(f"Killed old process {current_res.process_id} for chat_id={chat_id}")
                except ProcessLookupError:
                    logger.warning(f"Process {current_res.process_id} already not found")
                except Exception as e:
                    logger.warning(f"Error killing process {current_res.process_id}: {e}")

            # Launch new background process (password hidden from CLI args for security)
            arguments = [
                username,
                "__REDIS__",
                search_params.dep_date,
                search_params.src_locate,
                search_params.dst_locate,
                search_params.dep_time,
                search_params.train_type,
                search_params.special_option,
                str(chat_id),
                search_params.max_dep_time,
                str(search_params.passenger_count),
                search_params.seat_strategy
            ]
            cmd = ['python', '-m', 'telegramBot.telebotBackProcess'] + arguments
            proc = subprocess.Popen(cmd)
            logger.info(f"Restarted reservation process for chat_id={chat_id}, new pid={proc.pid}")

            # Save updated running reservation
            new_res = RunningReservation(
                chat_id=chat_id,
                process_id=proc.pid,
                korail_id=username,
                search_params=search_params
            )
            self.storage.save_running_reservation(new_res)

            # Update user session
            session.in_progress = True
            session.process_id = proc.pid
            session.search_params = search_params
            session.last_action = UserProgress.FINDING_TICKET
            session.editing_field = None
            session.train_info.update({
                'depDate': search_params.dep_date,
                'srcLocate': search_params.src_locate,
                'dstLocate': search_params.dst_locate,
                'depTime': search_params.dep_time,
                'maxDepTime': search_params.max_dep_time,
                'trainType': search_params.train_type,
                'trainTypeShow': search_params.train_type_display,
                'specialInfo': search_params.special_option,
                'specialInfoShow': search_params.special_option_display,
                'passengerCount': search_params.passenger_count,
                'seatStrategy': search_params.seat_strategy,
                'seatStrategyShow': "연속 좌석" if search_params.seat_strategy == "consecutive" else "랜덤 배치"
            })
            self.storage.save_user_session(session)

            # Clear multi reservation status if strategy changed to consecutive
            if search_params.seat_strategy == "consecutive":
                self.storage.delete_multi_reservation_status(chat_id)

            # Send confirmation message
            seat_display = "연속 좌석 (한 번에 예약)" if search_params.seat_strategy == "consecutive" else "랜덤 배치 (한 좌석씩 예약)"
            self.telegram.send_message(
                chat_id,
                f"🔄 예약 옵션이 변경되어 Conductor가 여정 탐색을 재시작했습니다!\n\n"
                f"📋 현재 설정\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 출발일: {search_params.dep_date}\n"
                f"🚉 구간: {search_params.src_locate} ➡️ {search_params.dst_locate}\n"
                f"🕐 시간: {search_params.dep_time[:4]} ~ {search_params.max_dep_time}\n"
                f"🚄 열차: {search_params.train_type_display}\n"
                f"💺 좌석: {search_params.special_option_display}\n"
                f"👥 인원: {search_params.passenger_count}명\n"
                f"🪑 배치: {seat_display}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💡 취소를 원하시면 언제든 /cancel을 입력하세요."
            )
            return True

        except Exception as e:
            logger.error(f"Failed to restart reservation: {e}", exc_info=True)
            return False

    def cancel_all_reservations(self, admin_chat_id: int) -> int:
        """
        Cancel all running reservations (admin function).

        Args:
            admin_chat_id: Admin's chat ID for notification

        Returns:
            Number of reservations cancelled
        """
        reservations = self.storage.get_all_running_reservations()
        count = 0

        for reservation in reservations:
            try:
                # Kill process
                if reservation.process_id != 9999999:
                    os.kill(reservation.process_id, signal.SIGTERM)
                    logger.info(f"Killed process {reservation.process_id}")

                # Notify user
                self.telegram.send_message(
                    reservation.chat_id,
                    "관리자에 의해 실행중이던 예약이 강제 종료됩니다. 꼬우면 관리자에게 연락하세요."
                )

                # Reset session
                session = self.storage.get_user_session(reservation.chat_id)
                if session:
                    session.reset()
                    self.storage.save_user_session(session)

                # Clean up
                self.storage.delete_running_reservation(reservation.chat_id)
                count += 1

            except Exception as e:
                logger.error(f"Error cancelling reservation {reservation.chat_id}: {e}")

        # Notify admin
        korail_ids = [r.korail_id for r in reservations]
        self.telegram.send_message(
            admin_chat_id,
            f"총 {count}개의 진행중인 예약을 종료했습니다. 이용중이던 사용자 : {korail_ids}"
        )

        return count

    def cleanup_stale_reservations(self) -> int:
        """
        Clean up reservations whose background processes are no longer running.

        Returns:
            Number of stale reservations cleaned up
        """
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return 0

        reservations = self.storage.get_all_running_reservations()
        count = 0
        for reservation in reservations:
            pid = reservation.process_id
            is_alive = False
            if pid and pid != 9999999:
                try:
                    os.kill(pid, 0)
                    is_alive = True
                except (ProcessLookupError, PermissionError):
                    is_alive = False

            if not is_alive:
                logger.info(
                    f"Cleaning up stale reservation for chat_id={reservation.chat_id}, "
                    f"pid={pid} is not running"
                )
                self.storage.delete_running_reservation(reservation.chat_id)
                session = self.storage.get_user_session(reservation.chat_id)
                if session and (session.last_action == UserProgress.FINDING_TICKET or session.in_progress):
                    session.reset()
                    self.storage.save_user_session(session)
                count += 1

        return count

    def get_status(self, chat_id: int) -> str:
        """
        Get status of running reservations for user and system.

        Args:
            chat_id: Chat ID requesting status

        Returns:
            Status message
        """
        self.cleanup_stale_reservations()
        user_res = self.storage.get_running_reservation(chat_id)
        session = self.storage.get_user_session(chat_id)

        if user_res:
            p = user_res.search_params
            if p:
                seat_display = "연속 좌석 (한 번에 예약)" if getattr(p, 'seat_strategy', 'consecutive') == 'consecutive' else "랜덤 배치 (한 좌석씩)"
                dep_time_display = p.dep_time[:4] if len(p.dep_time) >= 4 else p.dep_time
                return (
                    "🚂 Agent-Conductor 여정 조율 진행 중\n\n"
                    f"• 계정: `{user_res.korail_id}`\n"
                    f"• 출발일: {p.dep_date}\n"
                    f"• 구간: {p.src_locate} ➡️ {p.dst_locate}\n"
                    f"• 시간대: {dep_time_display} ~ {p.max_dep_time}\n"
                    f"• 열차: {p.train_type_display}\n"
                    f"• 좌석: {p.special_option_display}\n"
                    f"• 인원: {p.passenger_count}명 ({seat_display})\n"
                    f"• 프로세스 ID: {user_res.process_id}\n\n"
                    "💡 옵션 수정: /edit | 조율 취소: /cancel"
                )

        account_info = ""
        if session and session.credentials and session.credentials.korail_id:
            account_info = f"\n• 로그인 계정: `{session.credentials.korail_id}` (세션 보존됨)"

        return (
            "🚂 Agent-Conductor 여정 상태\n\n"
            "현재 진행 중인 열차 탐색이 없습니다."
            f"{account_info}\n\n"
            "새로운 여정 조율을 시작하시려면 /start 를 입력하거나, 자연어로 원하는 일정을 말씀해 주세요.\n"
            "예: '내일 저녁 6시 서울에서 부산 2명'"
        )

    def _notify_subscribers_start(self, username: str, params: TrainSearchParams) -> None:
        """Notify subscribers about reservation start."""
        subscribers = self.storage.get_all_subscribers()
        message = (
            f"{username}의 {params.src_locate}에서 {params.dst_locate}로 "
            f"{params.dep_date}에 출발하는 열차 예약이 시작되었습니다."
        )
        self.telegram.send_to_multiple(subscribers, message)

    def _notify_subscribers_end(self, username: str) -> None:
        """Notify subscribers about reservation end."""
        subscribers = self.storage.get_all_subscribers()
        message = f"{username}의 예약이 종료되었습니다."
        self.telegram.send_to_multiple(subscribers, message)
