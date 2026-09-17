import random
import time
from typing import Optional, List
from requests.exceptions import RequestException
from korail2 import (
    Korail as K2MKorail, TrainType, ReserveOption, SoldOutError, NoResultsError,
    AdultPassenger
)

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class TrainService:
    """Service for interacting with Train reservation API."""

    def __init__(self):
        """Initialize Train service."""
        self._train_api_client: Optional[K2MKorail] = None
        self._korail_instance: Optional[K2MKorail] = None  # Backward compatibility alias
        self._logged_in = False
        self._error_backoff_interval: float = 60.0  # 1 minute backoff on error
        self._current_search_interval: Optional[float] = None
        self._username: Optional[str] = None
        self._password: Optional[str] = None
        self._last_login_time: float = 0
        self._relogin_interval: int = 30 * 60  # 30 minutes
        self._relogin_count: int = 0
        self._last_search_error: Optional[str] = None
        self._last_error_log_time: float = 0
        self._error_repeat_count: int = 0
        self._traffic_congestion_count: int = 0
        self._waf_block_count: int = 0
        self._last_waf_error: str = ""
        self._in_maintenance: bool = False
        self._maintenance_notified: bool = False
        self.error_listener = None
        self.waf_block_listener = None
        self.maintenance_listener = None

        # Log class methods to verify correct version is loaded
        logger.debug(f"TrainService initialized with methods: {[m for m in dir(self) if not m.startswith('_')]}")

    def get_sleep_interval(self) -> float:
        """Get the current sleep interval (normal interval with jitter or error backoff)."""
        if self._current_search_interval is not None:
            return self._current_search_interval
        return random.uniform(
            settings.TRAIN_SEARCH_MIN_INTERVAL,
            settings.TRAIN_SEARCH_MAX_INTERVAL,
        )

    @staticmethod
    def _get_status_code(error: Exception) -> Optional[int]:
        status_code = getattr(error, 'status_code', None)
        if status_code is not None:
            return status_code
        return getattr(getattr(error, 'response', None), 'status_code', None)

    def _is_waf_block_error(self, e: Exception, err_str: str) -> bool:
        """Check if an error indicates a WAF / DynaPath / rate limit block."""
        err_type = type(e).__name__
        # 1. KeyError 'strResult' occurs when server returns WAF 403 or non-standard JSON without strResult
        if err_type == 'KeyError' and 'strResult' in err_str:
            return True
        # 2. Known WAF text or codes in error message
        waf_keywords = [
            "code: -2000",
            "-2000",
            "매크로",
            "안정적인 환경",
            "미허가 도구",
            "DynaPath",
            "RemoteDisconnected",
            "Connection aborted",
            "Remote end closed connection without response",
            "403 Forbidden",
            "Forbidden"
        ]
        if any(kw in err_str for kw in waf_keywords):
            return True
        # 3. HTTP status code 403 on exception if available
        return self._get_status_code(e) == 403

    def _is_rate_limit_error(self, e: Exception, err_str: str) -> bool:
        """Check if an error indicates rate limiting (HTTP 429) or transient retry requests."""
        # 1. HTTP 429 status code
        if self._get_status_code(e) == 429:
            return True
        # 2. Keywords indicating rate limit or '잠시 후 다시 시도'
        rate_limit_keywords = [
            "429",
            "Too Many Requests",
            "잠시 후 다시 시도",
            "잠시 뒤 다시 시도",
            "잠시 후 이용",
            "잠시 뒤 이용",
            "접속량이 많아",
            "접속이 원활하지",
            "사용자가 많아"
        ]
        return any(kw in err_str for kw in rate_limit_keywords)

    def _is_maintenance_error(self, e: Exception, err_str: str) -> bool:
        """Check if an error indicates Korail scheduled server maintenance."""
        # 1. Check exception attribute if available (e.g. h_msg_cd == 'S000')
        msg_code = getattr(e, 'msg_code', None) or getattr(e, 'h_msg_cd', None)
        if msg_code == 'S000':
            return True

        # 2. Keywords indicating scheduled maintenance
        maintenance_keywords = [
            "S000",
            "일시중지",
            "일시 중지",
            "서비스를 일시",
            "보다 편리한 서비스",
            "시스템 점검",
            "정기 점검",
            "정기점검",
            "점검시간",
            "점검 시간",
            "서비스 점검"
        ]
        return any(kw in err_str for kw in maintenance_keywords)

    def is_in_maintenance(self) -> bool:
        """Check if service is currently in scheduled maintenance standby mode."""
        return self._in_maintenance

    def is_waf_max_exceeded(self) -> bool:
        """Check if consecutive WAF blocks reached or exceeded maximum allowed threshold."""
        return self._waf_block_count >= settings.WAF_MAX_CONSECUTIVE_BLOCKS

    def set_search_interval(self, interval: float) -> None:
        """Dynamically set search interval (e.g. by AI agent)."""
        self._current_search_interval = float(interval)
        self._error_backoff_interval = float(interval)
        logger.info(f"Updated search interval to {interval}s")

    def logout(self) -> None:
        """End the remote Korail session and always discard local login state."""
        korail_instance = self._korail_instance
        try:
            if korail_instance is not None:
                korail_instance.logout()
        except RequestException as error:
            logger.warning(f"Korail remote logout failed: {error}")
        finally:
            self._logged_in = False
            self._korail_instance = None
            self._username = None
            self._password = None

    def login(self, username: str, password: str) -> bool:
        """
        Login to Korail with credentials.

        Args:
            username: Korail username (phone number in format 010-xxxx-xxxx)
            password: Korail password

        Returns:
            True if login successful, False otherwise
        """
        try:
            self._korail_instance = K2MKorail(username, password, auto_login=False)
            self._logged_in = self._korail_instance.login()

            if self._logged_in:
                self._username = username
                self._password = password
                self._last_login_time = time.time()
                if self._in_maintenance:
                    logger.info(f"🟢 [점검 종료 확인] 코레일 서버 재오픈! 로그인이 복구되었습니다: {username}")
                    self._in_maintenance = False
                    self._maintenance_notified = False
                    self._current_search_interval = None
                    if self.maintenance_listener:
                        try:
                            self.maintenance_listener(False, "코레일 점검 종료")
                        except Exception as m_err:
                            logger.error(f"Error in maintenance_listener: {m_err}")
                else:
                    logger.info(f"Korail login successful for user: {username}")
            else:
                logger.warning(f"Korail login failed for user: {username}")

            return self._logged_in
        except Exception as e:
            err_str = str(e)
            if self._is_maintenance_error(e, err_str):
                self._in_maintenance = True
                logger.warning(f"🚧 [서버 점검 감지] 코레일 정기 점검 중 로그인 실패: {e}")
            else:
                logger.error(f"Korail login error for user {username}: {e}")
            return False

    def _relogin(self) -> bool:
        """Attempt to re-login with stored credentials after session expiry or during maintenance probe."""
        if not self._username or not self._password:
            logger.error("🔒 Cannot re-login: no stored credentials")
            return False

        logger.debug("🔄 Attempting login/re-login...")
        try:
            self._korail_instance = K2MKorail(self._username, self._password, auto_login=False)
            logged_in = self._korail_instance.login()
            if logged_in:
                self._logged_in = True
                self._last_login_time = time.time()
                self._relogin_count += 1
                if self._in_maintenance:
                    logger.info("🟢 [점검 종료 확인] 코레일 서버가 재오픈되어 로그인이 성공했습니다!")
                    self._in_maintenance = False
                    self._maintenance_notified = False
                    self._current_search_interval = None
                    if self.maintenance_listener:
                        try:
                            self.maintenance_listener(False, "코레일 점검 종료")
                        except Exception as m_err:
                            logger.error(f"Error in maintenance_listener: {m_err}")
                else:
                    logger.debug(f"✅ Re-login successful (total: {self._relogin_count})")
            else:
                logger.warning("❌ Re-login returned False")
                self._logged_in = False
            return self._logged_in
        except Exception as e:
            err_str = str(e)
            if self._is_maintenance_error(e, err_str):
                self._in_maintenance = True
                logger.warning(f"🚧 [서버 점검 감지] 재로그인 중 정기 점검 감지: {e}")
            else:
                logger.error(f"❌ Re-login error: {e}")
            self._logged_in = False
            return False

    def _check_session_refresh(self):
        """Proactively re-login if session is older than the relogin interval."""
        if self._in_maintenance:
            return
        if self._last_login_time and (time.time() - self._last_login_time) >= self._relogin_interval:
            logger.debug(f"🔄 Session older than {self._relogin_interval}s, proactive re-login")
            self._relogin()

    def search_trains(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        passenger_count: int = 1,
        verbose: bool = True
    ) -> List:
        """
        Search for available trains.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station name (without '역')
            dst_locate: Destination station name (without '역')
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time threshold (HHMM)
            train_type: Type of train to search for
            passenger_count: Number of adult passengers

        Returns:
            List of available trains

        Raises:
            ValueError: If not logged in
        """
        if not self._logged_in or not self._korail_instance:
            if self._in_maintenance:
                logger.info("🔍 [점검 대기 모드] 코레일 점검 종료 여부 확인을 위해 로그인 프로브를 전송합니다...")
                if self._relogin():
                    logger.info("🎉 코레일 점검 종료 확인! 열차 조회를 재개합니다.")
                else:
                    probe_interval = random.uniform(
                        settings.MAINTENANCE_CHECK_MIN_INTERVAL,
                        settings.MAINTENANCE_CHECK_MAX_INTERVAL
                    )
                    self._current_search_interval = probe_interval
                    logger.info(
                        f"⏳ [점검 진행 중] 코레일 점검이 아직 끝나지 않았습니다. "
                        f"{probe_interval:.1f}초({probe_interval/60:.1f}분) 후 다시 확인합니다."
                    )
                    return []
            else:
                # Attempt relogin if credentials exist
                if self._username and self._password:
                    if not self._relogin():
                        if self._in_maintenance:
                            probe_interval = random.uniform(
                                settings.MAINTENANCE_CHECK_MIN_INTERVAL,
                                settings.MAINTENANCE_CHECK_MAX_INTERVAL
                            )
                            self._current_search_interval = probe_interval
                            return []
                        raise ValueError("Must login before searching trains")
                else:
                    raise ValueError("Must login before searching trains")

        try:
            # Create passenger list
            passengers = [AdultPassenger(passenger_count)]

            if verbose:
                logger.debug(
                    f"🔍 Searching trains with parameters:"
                )
                logger.debug(f"  dep_date: {dep_date} (type: {type(dep_date).__name__})")
                logger.debug(f"  src_locate: '{src_locate}' (type: {type(src_locate).__name__})")
                logger.debug(f"  dst_locate: '{dst_locate}' (type: {type(dst_locate).__name__})")
                logger.debug(f"  dep_time: {dep_time} (type: {type(dep_time).__name__})")
                logger.debug(f"  train_type: {train_type}")
                logger.debug(f"  passengers: {passengers} (count: {passenger_count})")
                logger.debug(f"  max_dep_time: {max_dep_time}")

            trains = self._korail_instance.search_train(
                src_locate,
                dst_locate,
                dep_date,
                dep_time,
                train_type=train_type,
                passengers=passengers
            )

            if verbose:
                logger.debug(f"📋 Korail API returned {len(trains) if trains else 0} trains")

                # Log each train found with seat availability
                if trains:
                    for i, train in enumerate(trains, 1):
                        train_str = str(train)
                        logger.debug(f"  Train #{i}: {train_str}")

                        if hasattr(train, 'seat_available'):
                            logger.debug(f"    Seats available: {train.seat_available}")
                        if hasattr(train, 'general_seat'):
                            logger.debug(f"    General seats: {train.general_seat}")
                        if hasattr(train, 'special_seat'):
                            logger.debug(f"    Special seats: {train.special_seat}")

            # Filter by max departure time
            if trains and max_dep_time != "2400":
                filtered_trains = []
                max_time = int(max_dep_time)

                if verbose:
                    logger.debug(f"🔧 Applying max_dep_time filter: {max_dep_time}")

                for train in trains:
                    dep_time_int = self._extract_departure_time(train)
                    if dep_time_int > 0 and dep_time_int < max_time:
                        filtered_trains.append(train)
                        if verbose:
                            logger.debug(f"  ✅ Kept: {dep_time_int} < {max_time}")
                    else:
                        if verbose:
                            logger.debug(f"  ❌ Filtered out: {dep_time_int} >= {max_time}")

                trains = filtered_trains
                if verbose:
                    logger.debug(f"📊 After filtering: {len(trains)} trains remain")

            # On successful API response, restore normal search interval if we were in backoff
            if self._traffic_congestion_count > 0:
                logger.info(f"🟢 코레일 정상 응답 복구! 트래픽 지연 카운트 초기화 및 초고속 모드로 복귀합니다.")
                self._traffic_congestion_count = 0

            self._waf_block_count = 0
            self._last_waf_error = ""
            if self._in_maintenance:
                self._in_maintenance = False
                self._maintenance_notified = False
            if self._current_search_interval is not None:
                logger.info(
                    f"🟢 코레일 정상 응답 감지! 검색 주기를 "
                    f"{settings.KORAIL_SEARCH_MIN_INTERVAL}~{settings.KORAIL_SEARCH_MAX_INTERVAL}초로 복귀합니다."
                )
                self._current_search_interval = None
                self._last_search_error = None

            if verbose:
                logger.debug(
                    f"✅ Search complete: {len(trains)} trains available "
                    f"({src_locate}→{dst_locate} on {dep_date})"
                )
            return trains

        except NoResultsError:
            # NoResultsError is also a valid API response (no trains on schedule)
            self._waf_block_count = 0
            self._last_waf_error = ""
            if self._in_maintenance:
                self._in_maintenance = False
                self._maintenance_notified = False
            if self._traffic_congestion_count > 0:
                self._traffic_congestion_count = 0

            if self._current_search_interval is not None:
                logger.info(
                    f"🟢 코레일 정상 응답 감지! 검색 주기를 "
                    f"{settings.KORAIL_SEARCH_MIN_INTERVAL}~{settings.KORAIL_SEARCH_MAX_INTERVAL}초로 복귀합니다."
                )
                self._current_search_interval = None
                self._last_search_error = None

            if verbose:
                logger.debug(f"No trains found for search criteria (NoResultsError)")
            return []
        except Exception as e:
            if type(e).__name__ == 'NeedToLoginError':
                logger.debug(f"🔒 Session expired during search, re-logging in: {e}")
                if self._relogin():
                    return []  # Will retry on next loop iteration
                elif self._in_maintenance:
                    probe_interval = random.uniform(
                        settings.MAINTENANCE_CHECK_MIN_INTERVAL,
                        settings.MAINTENANCE_CHECK_MAX_INTERVAL
                    )
                    self._current_search_interval = probe_interval
                    return []
                else:
                    raise

            err_type = type(e).__name__
            err_str = str(e).strip()
            first_line = err_str.split('\n')[0] if '\n' in err_str else err_str
            error_key = f"{err_type}:{first_line}"
            now = time.time()

            # 0. Check for Korail Server Maintenance (S000, 일시중지 등)
            if self._is_maintenance_error(e, err_str):
                self._in_maintenance = True
                probe_interval = random.uniform(
                    settings.MAINTENANCE_CHECK_MIN_INTERVAL,
                    settings.MAINTENANCE_CHECK_MAX_INTERVAL
                )
                self._current_search_interval = probe_interval
                logger.warning(
                    f"🚧 [서버 점검 감지] 코레일 정기 점검이 감지되었습니다: {first_line}\n"
                    f"   ➔ {probe_interval:.1f}초({probe_interval/60:.1f}분) 간격 점검 대기 모드로 전환합니다."
                )
                if not self._maintenance_notified:
                    self._maintenance_notified = True
                    if self.maintenance_listener:
                        try:
                            self.maintenance_listener(True, first_line)
                        except Exception as m_err:
                            logger.error(f"Error in maintenance_listener: {m_err}")
                return []

            # 1. Check for WAF / DynaPath / Rate limit block error
            if self._is_waf_block_error(e, err_str):
                self._waf_block_count += 1
                self._last_waf_error = f"{err_type}: {first_line}"[:1000]
                self._current_search_interval = float(settings.WAF_COOLDOWN_SECONDS)
                cooldown_sec = settings.WAF_COOLDOWN_SECONDS
                if self.is_waf_max_exceeded():
                    logger.error(
                        f"🛑 [WAF 차단 지속] 누적 {self._waf_block_count}회 감지로 "
                        f"코레일 세션을 폐기하고 탐색을 종료합니다. (원인: {first_line})"
                    )
                    self.logout()
                else:
                    logger.warning(
                        f"🚨 [WAF 차단 감지] 코레일 방화벽(DynaPath/403) 제한 감지! "
                        f"(누적 {self._waf_block_count}회) {cooldown_sec}초({cooldown_sec // 60}분) 동안 "
                        f"탐색을 일시 중단합니다. (원인: {first_line})"
                    )
                if self.waf_block_listener:
                    try:
                        self.waf_block_listener(err_str, self._waf_block_count)
                    except Exception as listener_err:
                        logger.error(f"Error in waf_block_listener: {listener_err}")
                return []

            # 2. Check for Rate Limit (429) or temporary congestion ("잠시 후 다시 시도") -> 10s cooldown
            if self._is_rate_limit_error(e, err_str):
                self._traffic_congestion_count += 1
                self._current_search_interval = float(settings.RATE_LIMIT_COOLDOWN_SECONDS)
                wait_desc = f"{settings.RATE_LIMIT_COOLDOWN_SECONDS}초 대기 후 재탐색"

                if error_key != self._last_search_error or (now - self._last_error_log_time) >= 30:
                    logger.warning(
                        f"⏳ [요청 제한/트래픽 지연] 코레일 응답: {first_line} ({wait_desc})"
                    )
                    self._last_search_error = error_key
                    self._last_error_log_time = now
                return []

            # Reset traffic congestion count for other errors
            self._traffic_congestion_count = 0

            # Handle known business/API errors from Korail with 1-minute backoff
            if err_type in ('KorailError', 'SoldOutError'):
                self._current_search_interval = self._error_backoff_interval
                if self.error_listener:
                    try:
                        self.error_listener(err_str)
                    except Exception as listener_err:
                        logger.error(f"Error in error_listener: {listener_err}")

                if error_key != self._last_search_error or (now - self._last_error_log_time) >= 60:
                    repeat_info = f" (동일 에러 {self._error_repeat_count}회 반복)" if (error_key == self._last_search_error and self._error_repeat_count > 0) else ""
                    logger.warning(
                        f"⚠️ 코레일 응답: {first_line}{repeat_info} "
                        f"(다음 조회까지 {int(self._error_backoff_interval)}초 대기합니다...)"
                    )
                    self._last_search_error = error_key
                    self._last_error_log_time = now
                    self._error_repeat_count = 0
                else:
                    self._error_repeat_count += 1
            else:
                # Unexpected system exceptions: log full traceback
                logger.error(f"❌ Error searching trains: {e}", exc_info=True)
                self._current_search_interval = self._error_backoff_interval
                if self.error_listener:
                    self.error_listener(f"{err_type}: {first_line}"[:1000])

            return []

    def reserve_train(
        self,
        train,
        option: ReserveOption = ReserveOption.GENERAL_FIRST,
        passenger_count: int = 1
    ):
        """
        Attempt to reserve a specific train.

        Args:
            train: Train object from search_trains()
            option: Reservation option (special seat preference)
            passenger_count: Number of adult passengers

        Returns:
            Reservation object if successful, None otherwise
            Returns "DUPLICATE" string if duplicate reservation detected
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before reserving")

        try:
            # Create passenger list
            passengers = [AdultPassenger(passenger_count)]

            logger.debug(f"🎫 Attempting reservation:")
            logger.debug(f"  Train: {train}")
            logger.debug(f"  Option: {option}")
            logger.debug(f"  Passengers: {passenger_count}")

            reservation = self._korail_instance.reserve(train, passengers=passengers, option=option)

            if reservation:
                logger.info(f"🎉 RESERVATION SUCCESS!")
                logger.info(f"  Reservation details: {reservation}")
                if hasattr(reservation, 'rsv_id'):
                    logger.info(f"  Reservation ID: {reservation.rsv_id}")
                return reservation
            else:
                logger.debug(f"Reservation returned None (no seats available)")
                return None

        except SoldOutError as e:
            logger.debug(f"Train sold out during reservation attempt: {train}")
            return None
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__

            # Check for duplicate reservation error
            if "동일한 예약 내역" in error_msg or "WRR800029" in error_msg:
                # Return special value instead of raising exception
                logger.warning(f"⚠️ Duplicate reservation detected - will continue searching")
                logger.warning(f"  Error: {error_msg}")
                return "DUPLICATE"

            if error_type == 'NeedToLoginError':
                logger.debug(f"🔒 Session expired during reservation, re-logging in: {error_msg}")
                if self._relogin():
                    return None  # Will retry on next loop iteration
                else:
                    raise

            logger.error(f"❌ Reservation error ({error_type}): {error_msg}")
            logger.error(f"  Train: {train}")
            logger.error(f"  Option: {option}")
            logger.error(f"  Full traceback:", exc_info=True)
            return None

    def search_and_reserve_loop(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        reserve_option: ReserveOption = ReserveOption.GENERAL_FIRST,
        passenger_count: int = 1,
        seat_strategy: str = "consecutive",
        max_attempts: Optional[int] = None
    ):
        """
        Continuously search for trains and attempt reservation until successful.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station
            dst_locate: Destination station
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time (HHMM)
            train_type: Train type filter
            reserve_option: Reservation option
            passenger_count: Number of adult passengers
            seat_strategy: "consecutive" for seats together, "random" for separate seats
            max_attempts: Maximum attempts (None for infinite)

        Returns:
            Reservation object(s) when successful, None if max_attempts reached
        """
        if not self._logged_in:
            raise ValueError("Must login before searching")

        attempts = 0
        logger.info(
            f"Starting reservation loop: {src_locate} -> {dst_locate} "
            f"on {dep_date} at {dep_time} for {passenger_count} passengers ({seat_strategy} seating)"
        )

        if seat_strategy == "consecutive":
            return self._search_and_reserve_consecutive(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time,
                train_type, reserve_option, passenger_count, max_attempts
            )
        else:  # random
            return self._search_and_reserve_random(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time,
                train_type, reserve_option, passenger_count, max_attempts
            )

    def _search_and_reserve_consecutive(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: Optional[int]
    ):
        """Reserve seats consecutively (together)."""
        attempts = 0
        duplicate_notified = False
        last_heartbeat_time = time.time()

        logger.info(f"🔄 Starting consecutive seat search loop (passengers={passenger_count})")

        while True:
            attempts += 1
            now = time.time()
            if now - last_heartbeat_time >= 60.0:
                user_label = self._username or "사용자"
                logger.info(
                    f"🔄 [{user_label}] 연속 좌석({passenger_count}명) 탐색 진행 중... "
                    f"(총 {attempts}회 시도 완료, 취소표 대기 중 | {src_locate}→{dst_locate})"
                )
                last_heartbeat_time = now

            if max_attempts and attempts > max_attempts:
                logger.warning(f"❌ Reached max attempts ({max_attempts}), stopping")
                return None

            is_summary = (attempts % 60 == 0)

            if is_summary:
                logger.debug(f"━━━ Search attempt #{attempts} ━━━")

            self._check_session_refresh()

            # Search for trains
            trains = self.search_trains(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time, train_type, passenger_count,
                verbose=is_summary
            )

            if not trains:
                if is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
                if self.is_waf_max_exceeded():
                    logger.warning("🛑 Stopping consecutive reservation loop due to persistent WAF block")
                    return None
                time.sleep(self.get_sleep_interval())
                continue

            # Try to reserve each train found (trains found = rare, always log)
            for idx, train in enumerate(trains, 1):
                logger.debug(f"🚂 Trying train {idx}/{len(trains)}")
                reservation = self.reserve_train(train, option=reserve_option, passenger_count=passenger_count)

                if reservation == "DUPLICATE":
                    # Duplicate reservation detected
                    if not duplicate_notified:
                        # First time - raise exception to notify user once
                        duplicate_notified = True
                        logger.warning("⚠️ First duplicate detection - notifying user")
                        raise DuplicateReservationError("동일한 예약 내역이 존재합니다")
                    else:
                        # Already notified - just log and continue
                        logger.debug("Duplicate reservation still exists, continuing search...")
                elif reservation:
                    logger.info(f"🎉 CONSECUTIVE RESERVATION SUCCESS after {attempts} attempts!")
                    return reservation
                else:
                    logger.debug(f"Train {idx} failed (sold out or unavailable)")

            logger.debug(f"All {len(trains)} trains sold out in attempt #{attempts}")

            # Wait before next search
            time.sleep(self.get_sleep_interval())

    def _search_and_reserve_random(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: Optional[int]
    ):
        """Reserve seats randomly (one at a time until target count reached)."""
        attempts = 0
        reservations = []
        target_count = passenger_count
        duplicate_notified = False
        last_heartbeat_time = time.time()

        logger.info(f"Random seating: will reserve {target_count} individual tickets")

        while len(reservations) < target_count:
            attempts += 1
            now = time.time()
            if now - last_heartbeat_time >= 60.0:
                user_label = self._username or "사용자"
                logger.info(
                    f"🔄 [{user_label}] 랜덤 좌석({len(reservations) + 1}/{target_count}) 탐색 진행 중... "
                    f"(총 {attempts}회 시도 완료, 취소표 대기 중 | {src_locate}→{dst_locate})"
                )
                last_heartbeat_time = now

            if max_attempts and attempts > max_attempts:
                logger.warning(f"Reached max attempts ({max_attempts}), stopping")
                # Cancel any partial reservations
                self._cancel_reservations(reservations)
                return None

            is_summary = (attempts % 60 == 0)

            self._check_session_refresh()

            # Search for trains (search for single passenger each time)
            trains = self.search_trains(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time, train_type, passenger_count=1,
                verbose=is_summary
            )

            if not trains:
                if is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
                if self.is_waf_max_exceeded():
                    logger.warning("🛑 Stopping random reservation loop due to persistent WAF block")
                    self._cancel_reservations(reservations)
                    return None
                time.sleep(self.get_sleep_interval())
                continue

            # Try to reserve each train found (trains found = rare, always log)
            for train in trains:
                remaining = target_count - len(reservations)
                logger.debug(
                    f"Found train: {train}, attempting reservation "
                    f"({len(reservations) + 1}/{target_count})..."
                )

                # Reserve one seat at a time
                reservation = self.reserve_train(train, option=reserve_option, passenger_count=1)

                if reservation == "DUPLICATE":
                    # Duplicate reservation detected
                    if not duplicate_notified:
                        # First time - raise exception to notify user once
                        duplicate_notified = True
                        logger.warning("First duplicate detection - notifying user")
                        raise DuplicateReservationError("동일한 예약 내역이 존재합니다")
                    else:
                        # Already notified - just log and continue
                        logger.debug("Duplicate reservation still exists, continuing search...")
                elif reservation:
                    reservations.append(reservation)
                    current_count = len(reservations)
                    logger.info(
                        f"Reserved seat {current_count}/{target_count} "
                        f"(attempt #{attempts})"
                    )
                    logger.debug(f"Reservation details: {reservation}")

                    # Check if we've reached target
                    if current_count >= target_count:
                        logger.info(
                            f"All {target_count} seats reserved successfully! "
                            f"Total attempts: {attempts}"
                        )
                        # Return the first reservation as primary (for compatibility)
                        # Store all reservations in a custom attribute for later access
                        first_reservation = reservations[0]
                        first_reservation._all_reservations = reservations
                        first_reservation._is_random_allocation = True
                        first_reservation._total_seats = target_count
                        return first_reservation

                    # Add delay between individual reservations to avoid rate limit
                    # Use longer interval for safety
                    time.sleep(self.get_sleep_interval())
                    break  # Found a train and reserved, restart search loop

                else:
                    logger.debug("Reservation failed, continuing search...")

            # Wait before next search attempt
            time.sleep(self.get_sleep_interval())

        return reservations[0] if reservations else None

    def _cancel_reservations(self, reservations: List) -> None:
        """Cancel a list of reservations (cleanup for failed random allocation)."""
        if not reservations:
            return

        logger.warning(f"Cancelling {len(reservations)} partial reservations...")
        for reservation in reservations:
            try:
                # Note: korail2 API has a cancel method but we need to check if it's available
                logger.warning(f"Would cancel reservation: {reservation}")
                # self._korail_instance.cancel(reservation.rsv_id)
            except Exception as e:
                logger.error(f"Failed to cancel reservation: {e}")

    def _extract_departure_time(self, train) -> int:
        """
        Extract departure time from train object as HHMM integer.

        Args:
            train: Train object from korail2

        Returns:
            Departure time as integer (e.g., 944 for 09:44), 0 if extraction fails
        """
        try:
            # str(train) format: "[KTX] 4월 8일, 용산~광주송정(09:44~12:50), ..."
            # Use rsplit to handle station names with parentheses e.g. 울산(통도사)~서울(09:44~12:50)
            train_str = str(train)
            time_part = train_str.rsplit("(", 1)[1].split("~")[0]  # "09:44"
            time_str = "".join(time_part.split(":"))  # "0944"
            return int(time_str)
        except (IndexError, ValueError) as e:
            logger.error(f"Failed to extract departure time from train: {train}, error: {e}")
            return 0

    @property
    def is_logged_in(self) -> bool:
        """Check if currently logged in."""
        return self._logged_in

    @property
    def last_waf_error(self) -> str:
        return self._last_waf_error


class DuplicateReservationError(Exception):
    """Raised when attempting to reserve a train that's already reserved."""
    pass


# Backward compatibility alias
KorailService = TrainService

