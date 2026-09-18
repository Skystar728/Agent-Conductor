"""Redis storage implementation."""
from datetime import datetime
import json
import time
from typing import Optional, List
import redis

from models import (
    UserSession, RunningReservation, PaymentStatus,
    MultiReservationStatus, UserProgress, UserCredentials, TrainSearchParams,
    SingleReservationInfo, ReservationPaymentStatus
)
from storage.base import StorageInterface
from config.settings import settings
from utils.logger import get_logger
from utils.crypto import CryptoUtil

logger = get_logger(__name__)


class RedisStorage(StorageInterface):
    """
    Redis-based storage implementation.

    Provides persistent, process-shared state management using Redis.
    All data survives application restarts and is accessible across processes.
    """

    def __init__(self):
        """Initialize Redis connection pool."""
        try:
            self.redis = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD,
                decode_responses=settings.REDIS_DECODE_RESPONSES,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                max_connections=settings.REDIS_MAX_CONNECTIONS
            )
            # Test connection
            self.redis.ping()
            logger.info(f"Redis connected: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
        except redis.RedisError as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    # ==================== User Session Management ====================

    def get_user_session(self, chat_id: int) -> Optional[UserSession]:
        """Get user session by chat ID."""
        key = f"user_session:{chat_id}"
        data = self.redis.get(key)
        if not data:
            return None

        try:
            session_dict = json.loads(data)
            return self._deserialize_user_session(session_dict)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to deserialize user session: {e}")
            return None

    def save_user_session(self, session: UserSession, ttl_seconds: int = 86400) -> None:
        """Save or update user session with auto-expiry TTL (default 24h for active sessions)."""
        key = f"user_session:{session.chat_id}"
        data = json.dumps(self._serialize_user_session(session))
        # Keep user session short-lived to prevent forgotten lingering sessions
        if ttl_seconds:
            self.redis.setex(key, ttl_seconds, data)
        else:
            self.redis.set(key, data)
        logger.debug(f"Saved user session for chat_id={session.chat_id}")

    def clear_user_credentials(self, chat_id: int) -> None:
        """Wipe password and credentials immediately from stored session."""
        session = self.get_user_session(chat_id)
        if session:
            session.credentials = None
            self.save_user_session(session)
            logger.info(f"Cleared user credentials from session for chat_id={chat_id}")

    def delete_user_session(self, chat_id: int) -> None:
        """Delete user session."""
        key = f"user_session:{chat_id}"
        self.redis.delete(key)

    def get_all_user_sessions(self) -> List[UserSession]:
        """Get all user sessions."""
        keys = self.redis.keys("user_session:*")
        sessions = []
        for key in keys:
            data = self.redis.get(key)
            if data:
                try:
                    session_dict = json.loads(data)
                    sessions.append(self._deserialize_user_session(session_dict))
                except (json.JSONDecodeError, KeyError):
                    continue
        return sessions

    # ==================== Running Reservation Management ====================

    def get_running_reservation(self, chat_id: int) -> Optional[RunningReservation]:
        """Get running reservation by chat ID."""
        key = f"running_reservation:{chat_id}"
        data = self.redis.get(key)
        if not data:
            return None

        try:
            res_dict = json.loads(data)
            return self._deserialize_running_reservation(res_dict)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to deserialize running reservation: {e}")
            return None

    def save_running_reservation(self, reservation: RunningReservation) -> None:
        """Save running reservation."""
        key = f"running_reservation:{reservation.chat_id}"
        data = json.dumps(self._serialize_running_reservation(reservation))
        self.redis.set(key, data)

    def delete_running_reservation(self, chat_id: int) -> None:
        """Delete running reservation."""
        key = f"running_reservation:{chat_id}"
        self.redis.delete(key)

    def get_all_running_reservations(self) -> List[RunningReservation]:
        """Get all running reservations."""
        keys = self.redis.keys("running_reservation:*")
        reservations = []
        for key in keys:
            data = self.redis.get(key)
            if data:
                try:
                    res_dict = json.loads(data)
                    reservations.append(self._deserialize_running_reservation(res_dict))
                except (json.JSONDecodeError, KeyError):
                    continue
        return reservations

    # ==================== Payment Status Management ====================

    def get_payment_status(self, chat_id: int) -> Optional[PaymentStatus]:
        """Get payment status by chat ID."""
        key = f"payment_status:{chat_id}"
        data = self.redis.get(key)
        if not data:
            return None

        try:
            status_dict = json.loads(data)
            return self._deserialize_payment_status(status_dict)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to deserialize payment status: {e}")
            return None

    def save_payment_status(self, status: PaymentStatus) -> None:
        """Save payment status."""
        key = f"payment_status:{status.chat_id}"
        data = json.dumps(self._serialize_payment_status(status))
        # Set with TTL (payment timeout + buffer)
        ttl = (settings.PAYMENT_TIMEOUT_MINUTES + 5) * 60
        self.redis.setex(key, ttl, data)

    def delete_payment_status(self, chat_id: int) -> None:
        """Delete payment status."""
        key = f"payment_status:{chat_id}"
        self.redis.delete(key)

    def get_all_payment_statuses(self) -> List[PaymentStatus]:
        """Get all payment statuses."""
        keys = self.redis.keys("payment_status:*")
        statuses = []
        for key in keys:
            data = self.redis.get(key)
            if data:
                try:
                    status_dict = json.loads(data)
                    statuses.append(self._deserialize_payment_status(status_dict))
                except (json.JSONDecodeError, KeyError):
                    continue
        return statuses

    # ==================== Subscriber Management ====================

    def is_subscriber(self, chat_id: int) -> bool:
        """Check if user is a subscriber."""
        return self.redis.sismember("subscribers", str(chat_id))

    def add_subscriber(self, chat_id: int) -> None:
        """Add subscriber."""
        self.redis.sadd("subscribers", str(chat_id))

    def remove_subscriber(self, chat_id: int) -> None:
        """Remove subscriber."""
        self.redis.srem("subscribers", str(chat_id))

    def get_all_subscribers(self) -> List[int]:
        """Get all subscriber chat IDs."""
        members = self.redis.smembers("subscribers")
        return [int(m) for m in members]

    # ==================== Admin Management ====================

    def is_admin_authenticated(self, chat_id: int) -> bool:
        """Check if user is authenticated as admin."""
        key = f"admin_authenticated:{chat_id}"
        return bool(self.redis.get(key))

    def set_admin_authenticated(self, chat_id: int, authenticated: bool = True) -> None:
        """Set admin authentication status for chat ID."""
        key = f"admin_authenticated:{chat_id}"
        if authenticated:
            # Set with TTL (1 hour)
            self.redis.setex(key, 3600, "1")
        else:
            self.redis.delete(key)

    def is_waiting_for_admin_password(self, chat_id: int) -> bool:
        """Check if user is waiting to enter admin password."""
        key = f"admin_password_pending:{chat_id}"
        return bool(self.redis.get(key))

    def set_waiting_for_admin_password(self, chat_id: int, waiting: bool = True) -> None:
        """Set whether user is waiting to enter admin password."""
        key = f"admin_password_pending:{chat_id}"
        if waiting:
            self.redis.setex(key, 300, "1")  # 5 min TTL
        else:
            self.redis.delete(key)

    def get_pending_admin_command(self, chat_id: int) -> Optional[str]:
        """Get pending admin command waiting for authentication."""
        key = f"pending_admin_command:{chat_id}"
        return self.redis.get(key)

    def set_pending_admin_command(self, chat_id: int, command: Optional[str]) -> None:
        """Set pending admin command waiting for authentication."""
        key = f"pending_admin_command:{chat_id}"
        if command:
            self.redis.setex(key, 300, command)  # 5 min TTL
        else:
            self.redis.delete(key)

    # ==================== Multi-Reservation Status Management ====================

    def get_multi_reservation_status(self, chat_id: int) -> Optional[MultiReservationStatus]:
        """Get multi-reservation status by chat ID."""
        key = f"multi_reservation_status:{chat_id}"
        data = self.redis.get(key)
        if not data:
            return None

        try:
            status_dict = json.loads(data)
            return self._deserialize_multi_reservation_status(status_dict)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to deserialize multi reservation status: {e}")
            return None

    def save_multi_reservation_status(self, status: MultiReservationStatus) -> None:
        """Save multi-reservation status."""
        key = f"multi_reservation_status:{status.chat_id}"
        data = json.dumps(self._serialize_multi_reservation_status(status))
        # Set with TTL
        ttl = (settings.PAYMENT_TIMEOUT_MINUTES + 5) * 60
        self.redis.setex(key, ttl, data)

    def delete_multi_reservation_status(self, chat_id: int) -> None:
        """Delete multi-reservation status and related keys."""
        # Delete main status
        key = f"multi_reservation_status:{chat_id}"
        self.redis.delete(key)

        # Delete current seat index
        seat_key = f"current_seat_index:{chat_id}"
        self.redis.delete(seat_key)

        # Delete all payment ready flags for this user
        payment_keys = self.redis.keys(f"payment_ready:{chat_id}:*")
        if payment_keys:
            self.redis.delete(*payment_keys)

    def get_all_multi_reservation_statuses(self) -> List[MultiReservationStatus]:
        """Get all multi-reservation statuses."""
        keys = self.redis.keys("multi_reservation_status:*")
        statuses = []
        for key in keys:
            data = self.redis.get(key)
            if data:
                try:
                    status_dict = json.loads(data)
                    statuses.append(self._deserialize_multi_reservation_status(status_dict))
                except (json.JSONDecodeError, KeyError):
                    continue
        return statuses

    # ==================== Partial Reservation Management (Random Seating) ====================

    def save_partial_reservation(self, chat_id: int, seat_index: int, reservation_data: dict) -> None:
        """
        Save a partial reservation for random seating.

        Args:
            chat_id: User chat ID
            seat_index: Index of the seat (0-based)
            reservation_data: Serialized reservation information
        """
        key = f"partial_reservations:{chat_id}"
        # Store as JSON array
        existing = self.redis.get(key)
        reservations = json.loads(existing) if existing else []

        # Add or update
        while len(reservations) <= seat_index:
            reservations.append(None)
        reservations[seat_index] = reservation_data

        data = json.dumps(reservations)
        # TTL: 2 hours (enough for multiple reservations)
        self.redis.setex(key, 7200, data)
        logger.info(f"Saved partial reservation {seat_index} for chat_id={chat_id}")

    def get_partial_reservations(self, chat_id: int) -> List[dict]:
        """Get all partial reservations for a chat_id."""
        key = f"partial_reservations:{chat_id}"
        data = self.redis.get(key)
        if not data:
            return []

        try:
            reservations = json.loads(data)
            # Filter out None values
            return [r for r in reservations if r is not None]
        except json.JSONDecodeError:
            return []

    def delete_partial_reservations(self, chat_id: int) -> None:
        """Delete all partial reservations for a chat_id."""
        key = f"partial_reservations:{chat_id}"
        self.redis.delete(key)

    def get_current_seat_index(self, chat_id: int) -> Optional[int]:
        """Get the current seat index being reserved (for random seating)."""
        key = f"current_seat_index:{chat_id}"
        value = self.redis.get(key)
        return int(value) if value is not None else None

    def set_current_seat_index(self, chat_id: int, index: Optional[int]) -> None:
        """Set the current seat index being reserved."""
        key = f"current_seat_index:{chat_id}"
        if index is not None:
            self.redis.setex(key, 7200, str(index))  # 2 hour TTL
        else:
            self.redis.delete(key)

    def is_payment_ready(self, chat_id: int, seat_index: int) -> bool:
        """Check if payment is ready for a specific seat."""
        key = f"payment_ready:{chat_id}:{seat_index}"
        return bool(self.redis.get(key))

    def mark_payment_ready(self, chat_id: int, seat_index: int) -> None:
        """Mark payment as ready for a specific seat."""
        key = f"payment_ready:{chat_id}:{seat_index}"
        self.redis.setex(key, 60, "1")  # 60s TTL
        logger.info(f"Marked payment ready for seat {seat_index}, chat_id={chat_id}")

    def wait_for_payment(self, chat_id: int, seat_index: int, timeout: int = 600) -> bool:
        """
        Wait for payment confirmation with polling.

        Args:
            chat_id: User chat ID
            seat_index: Seat index (0-based)
            timeout: Maximum wait time in seconds (default 10 minutes)

        Returns:
            True if payment confirmed within timeout, False otherwise
        """
        key = f"payment_ready:{chat_id}:{seat_index}"
        start_time = time.time()

        logger.info(f"Waiting for payment confirmation (seat {seat_index}, timeout={timeout}s)...")

        while time.time() - start_time < timeout:
            # Check if payment flag is set
            if self.redis.get(key):
                # Delete the flag
                self.redis.delete(key)
                elapsed = int(time.time() - start_time)
                logger.info(f"Payment confirmed after {elapsed}s")
                return True

            # Sleep 1 second
            time.sleep(1)

            # Log every 30 seconds
            elapsed = int(time.time() - start_time)
            if elapsed % 30 == 0 and elapsed > 0:
                remaining = timeout - elapsed
                logger.debug(f"Still waiting for payment... {remaining}s remaining")

        logger.warning(f"Payment timeout after {timeout}s")
        return False

    # ==================== Serialization Helpers ====================

    def _serialize_user_session(self, session: UserSession) -> dict:
        """Serialize UserSession to dict."""
        return {
            "chat_id": session.chat_id,
            "in_progress": session.in_progress,
            "last_action": session.last_action,
            "process_id": session.process_id,
            "train_info": session.train_info,
            "credentials": {
                "train_id": session.credentials.train_id,
                "korail_id": session.credentials.korail_id,
                "train_pw": CryptoUtil.encrypt(session.credentials.train_pw),
                "korail_pw": CryptoUtil.encrypt(session.credentials.korail_pw)
            } if session.credentials else None,
            "search_params": {
                "dep_date": session.search_params.dep_date,
                "src_locate": session.search_params.src_locate,
                "dst_locate": session.search_params.dst_locate,
                "dep_time": session.search_params.dep_time,
                "max_dep_time": session.search_params.max_dep_time,
                "train_type": session.search_params.train_type,
                "train_type_display": session.search_params.train_type_display,
                "special_option": session.search_params.special_option,
                "special_option_display": session.search_params.special_option_display,
                "passenger_count": session.search_params.passenger_count,
                "seat_strategy": session.search_params.seat_strategy
            } if session.search_params else None,
            "editing_field": session.editing_field
        }

    def _deserialize_user_session(self, data: dict) -> UserSession:
        """Deserialize dict to UserSession."""
        credentials = None
        if data.get("credentials"):
            c = data["credentials"]
            raw_id = c.get("train_id") or c.get("korail_id", "")
            raw_pw = c.get("train_pw") or c.get("korail_pw", "")
            credentials = UserCredentials(
                train_id=raw_id,
                train_pw=CryptoUtil.decrypt(raw_pw)
            )

        search_params = None
        if data.get("search_params"):
            p = data["search_params"]
            search_params = TrainSearchParams(
                dep_date=p["dep_date"],
                src_locate=p["src_locate"],
                dst_locate=p["dst_locate"],
                dep_time=p["dep_time"],
                max_dep_time=p["max_dep_time"],
                train_type=p["train_type"],
                train_type_display=p.get("train_type_display", "Flagship (플래그십)" if "KTX" in str(p["train_type"]).upper() or "FLAGSHIP" in str(p["train_type"]).upper() else "Entry (엔트리)"),
                special_option=p["special_option"],
                special_option_display=p.get("special_option_display", p["special_option"]),
                passenger_count=p["passenger_count"],
                seat_strategy=p["seat_strategy"]
            )

        return UserSession(
            chat_id=data["chat_id"],
            in_progress=data["in_progress"],
            last_action=data["last_action"],
            process_id=data.get("process_id", 9999999),
            train_info=data.get("train_info", {}),
            credentials=credentials,
            search_params=search_params,
            editing_field=data.get("editing_field")
        )

    def _serialize_running_reservation(self, reservation: RunningReservation) -> dict:
        """Serialize RunningReservation to dict."""
        return {
            "chat_id": reservation.chat_id,
            "process_id": reservation.process_id,
            "train_id": reservation.train_id,
            "korail_id": reservation.korail_id,
            "search_params": {
                "dep_date": reservation.search_params.dep_date,
                "src_locate": reservation.search_params.src_locate,
                "dst_locate": reservation.search_params.dst_locate,
                "dep_time": reservation.search_params.dep_time,
                "max_dep_time": reservation.search_params.max_dep_time,
                "train_type": reservation.search_params.train_type,
                "special_option": reservation.search_params.special_option,
                "passenger_count": reservation.search_params.passenger_count,
                "seat_strategy": reservation.search_params.seat_strategy
            }
        }

    def _deserialize_running_reservation(self, data: dict) -> RunningReservation:
        """Deserialize dict to RunningReservation."""
        p = data["search_params"]
        search_params = TrainSearchParams(
            dep_date=p["dep_date"],
            src_locate=p["src_locate"],
            dst_locate=p["dst_locate"],
            dep_time=p["dep_time"],
            max_dep_time=p["max_dep_time"],
            train_type=p["train_type"],
            special_option=p["special_option"],
            passenger_count=p["passenger_count"],
            seat_strategy=p["seat_strategy"]
        )

        raw_id = data.get("train_id") or data.get("korail_id", "")
        return RunningReservation(
            chat_id=data["chat_id"],
            process_id=data["process_id"],
            train_id=raw_id,
            korail_id=raw_id,
            search_params=search_params
        )

    def _serialize_payment_status(self, status: PaymentStatus) -> dict:
        """Serialize PaymentStatus to dict."""
        return {
            "chat_id": status.chat_id,
            "reminder_active": status.reminder_active,
            "completed": status.completed,
            "reservation_time": status.reservation_time.isoformat() if status.reservation_time else None
        }

    def _deserialize_payment_status(self, data: dict) -> PaymentStatus:
        """Deserialize dict to PaymentStatus."""
        return PaymentStatus(
            chat_id=data["chat_id"],
            reminder_active=data["reminder_active"],
            completed=data["completed"],
            reservation_time=datetime.fromisoformat(data["reservation_time"]) if data.get("reservation_time") else None
        )

    def _serialize_multi_reservation_status(self, status: MultiReservationStatus) -> dict:
        """Serialize MultiReservationStatus to dict."""
        return {
            "chat_id": status.chat_id,
            "reservations": [
                {
                    "reservation_id": r.reservation_id,
                    "reserved_at": r.reserved_at.isoformat(),
                    "expires_at": r.expires_at.isoformat(),
                    "status": r.status.value if hasattr(r.status, 'value') else r.status,
                    "seat_number": r.seat_number,
                    "train_info": r.train_info
                } for r in status.reservations
            ],
            "total_seats": status.total_seats,
            "seat_strategy": status.seat_strategy,
            "created_at": status.created_at.isoformat(),
            "manually_stopped": status.manually_stopped
        }

    def _deserialize_multi_reservation_status(self, data: dict) -> MultiReservationStatus:
        """Deserialize dict to MultiReservationStatus."""
        reservations = [
            SingleReservationInfo(
                reservation_id=r["reservation_id"],
                reservation_obj=None,  # Can't serialize actual reservation object
                reserved_at=datetime.fromisoformat(r["reserved_at"]),
                expires_at=datetime.fromisoformat(r["expires_at"]),
                status=ReservationPaymentStatus(r["status"]) if isinstance(r["status"], str) else r["status"],
                seat_number=r["seat_number"],
                train_info=r["train_info"]
            ) for r in data["reservations"]
        ]

        return MultiReservationStatus(
            chat_id=data["chat_id"],
            reservations=reservations,
            total_seats=data["total_seats"],
            seat_strategy=data["seat_strategy"],
            created_at=datetime.fromisoformat(data["created_at"]),
            manually_stopped=data["manually_stopped"]
        )

    # ==================== Admin Operations ====================

    def flush_all(self) -> int:
        """
        Flush all Redis data (admin operation).

        WARNING: This will delete ALL data from the Redis database.
        Use with extreme caution.

        Returns:
            Number of keys deleted
        """
        try:
            # Get count before flushing
            key_count = self.redis.dbsize()

            # Flush all data in current database
            self.redis.flushdb()

            logger.warning(f"Redis database flushed. {key_count} keys deleted.")
            return key_count
        except redis.RedisError as e:
            logger.error(f"Failed to flush Redis: {e}")
            raise

    # ==================== Debug Mode Management ====================

    def is_debug_mode(self) -> bool:
        """Check if global debug mode is enabled."""
        return self.redis.get("debug_mode:global") == "1"

    def set_debug_mode(self, enabled: bool) -> None:
        """Enable or disable global debug mode."""
        if enabled:
            self.redis.set("debug_mode:global", "1")
            logger.info("Global debug mode enabled")
        else:
            self.redis.delete("debug_mode:global")
            logger.info("Global debug mode disabled")

    # ==================== AI Error Policy & HITL Management ====================

    def get_error_policy(self, error_hash: str) -> Optional[dict]:
        """Get cached error policy by error hash."""
        key = f"error_policy:{error_hash}"
        data = self.redis.get(key)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse error policy {key}: {e}")
            return None

    def save_error_policy(self, error_hash: str, policy: dict, ttl: int = 604800) -> None:
        """Save error policy with TTL (default 7 days)."""
        key = f"error_policy:{error_hash}"
        try:
            self.redis.set(key, json.dumps(policy, ensure_ascii=False), ex=ttl)
            logger.info(f"Saved error policy for hash {error_hash} (TTL={ttl}s)")
        except redis.RedisError as e:
            logger.error(f"Failed to save error policy {key}: {e}")

    def is_user_notified(self, chat_id: int, error_hash: str) -> bool:
        """Check if user has already been notified of this error."""
        key = f"user_notified:{chat_id}:{error_hash}"
        return bool(self.redis.exists(key))

    def mark_user_notified(self, chat_id: int, error_hash: str, ttl: int = 3600) -> None:
        """Mark user as notified of this error to prevent spam (default 1 hour)."""
        key = f"user_notified:{chat_id}:{error_hash}"
        try:
            self.redis.set(key, "1", ex=ttl)
        except redis.RedisError as e:
            logger.error(f"Failed to mark user notified {key}: {e}")

    def set_hitl_request(self, chat_id: int, request_data: dict, ttl: int = 300) -> None:
        """Set active HITL decision request for user."""
        key = f"hitl_request:{chat_id}"
        decision_key = f"hitl_decision:{chat_id}"
        try:
            self.redis.delete(decision_key)
            self.redis.set(key, json.dumps(request_data, ensure_ascii=False), ex=ttl)
            logger.info(f"Set HITL request for chat_id={chat_id} (TTL={ttl}s)")
        except redis.RedisError as e:
            logger.error(f"Failed to set HITL request for chat_id={chat_id}: {e}")

    def get_hitl_request(self, chat_id: int) -> Optional[dict]:
        """Get active HITL decision request for user."""
        key = f"hitl_request:{chat_id}"
        data = self.redis.get(key)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse HITL request {key}: {e}")
            return None

    def delete_hitl_request(self, chat_id: int) -> None:
        """Delete active HITL request."""
        key = f"hitl_request:{chat_id}"
        try:
            self.redis.delete(key)
        except redis.RedisError as e:
            logger.error(f"Failed to delete HITL request {key}: {e}")

    def set_hitl_decision(self, chat_id: int, chosen_option: int) -> None:
        """Record user's decision for active HITL request."""
        decision_key = f"hitl_decision:{chat_id}"
        try:
            self.redis.set(decision_key, str(chosen_option), ex=300)
            self.delete_hitl_request(chat_id)
            logger.info(f"Recorded HITL decision {chosen_option} for chat_id={chat_id}")
        except redis.RedisError as e:
            logger.error(f"Failed to set HITL decision {decision_key}: {e}")

    def get_hitl_decision(self, chat_id: int) -> Optional[int]:
        """Get user's recorded decision."""
        decision_key = f"hitl_decision:{chat_id}"
        val = self.redis.get(decision_key)
        if val is not None:
            try:
                return int(val)
            except ValueError:
                return None
        return None

    def wait_for_hitl_decision(self, chat_id: int, timeout: int = 300, default_option: int = 1) -> int:
        """
        Wait for user's decision, polling Redis until timeout.
        Returns chosen option id, or default_option if timed out.
        """
        start_time = time.time()
        logger.info(f"Waiting for HITL decision from chat_id={chat_id} (timeout={timeout}s, default={default_option})")
        while time.time() - start_time < timeout:
            decision = self.get_hitl_decision(chat_id)
            if decision is not None:
                logger.info(f"Received HITL decision {decision} from chat_id={chat_id}")
                return decision
            time.sleep(1.0)

        logger.warning(f"HITL decision timeout for chat_id={chat_id}, falling back to default option {default_option}")
        self.delete_hitl_request(chat_id)
        return default_option

    # ==================== Last Search Params & Payment Retry Management ====================

    def _serialize_search_params(self, params: TrainSearchParams) -> dict:
        """Serialize TrainSearchParams to dict."""
        return {
            "dep_date": params.dep_date,
            "src_locate": params.src_locate,
            "dst_locate": params.dst_locate,
            "dep_time": params.dep_time,
            "max_dep_time": params.max_dep_time,
            "train_type": params.train_type,
            "train_type_display": params.train_type_display,
            "special_option": params.special_option,
            "special_option_display": params.special_option_display,
            "passenger_count": params.passenger_count,
            "seat_strategy": params.seat_strategy
        }

    def _deserialize_search_params(self, data: dict) -> TrainSearchParams:
        """Deserialize dict to TrainSearchParams."""
        return TrainSearchParams(
            dep_date=data["dep_date"],
            src_locate=data["src_locate"],
            dst_locate=data["dst_locate"],
            dep_time=data["dep_time"],
            max_dep_time=data["max_dep_time"],
            train_type=data["train_type"],
            train_type_display=data["train_type_display"],
            special_option=data["special_option"],
            special_option_display=data["special_option_display"],
            passenger_count=data["passenger_count"],
            seat_strategy=data["seat_strategy"]
        )

    def save_last_search_params(self, chat_id: int, params: TrainSearchParams) -> None:
        """Save search parameters for possible restart on payment timeout."""
        key = f"last_search_params:{chat_id}"
        try:
            data = self._serialize_search_params(params)
            self.redis.set(key, json.dumps(data), ex=86400)  # 24h TTL
            logger.info(f"Saved last search params for chat_id={chat_id}")
        except redis.RedisError as e:
            logger.error(f"Failed to save last search params for chat_id={chat_id}: {e}")

    def get_last_search_params(self, chat_id: int) -> Optional[TrainSearchParams]:
        """Get saved search parameters."""
        key = f"last_search_params:{chat_id}"
        try:
            data = self.redis.get(key)
            if data:
                return self._deserialize_search_params(json.loads(data))
            return None
        except (redis.RedisError, json.JSONDecodeError) as e:
            logger.error(f"Failed to get last search params for chat_id={chat_id}: {e}")
            return None

    def delete_last_search_params(self, chat_id: int) -> None:
        """Delete saved search parameters."""
        key = f"last_search_params:{chat_id}"
        try:
            self.redis.delete(key)
            logger.debug(f"Deleted last search params for chat_id={chat_id}")
        except redis.RedisError as e:
            logger.error(f"Failed to delete last search params for chat_id={chat_id}: {e}")

    def get_retry_count(self, chat_id: int) -> int:
        """Get automatic retry count for payment timeout."""
        key = f"payment_retry_count:{chat_id}"
        try:
            val = self.redis.get(key)
            return int(val) if val else 0
        except (redis.RedisError, ValueError):
            return 0

    def increment_retry_count(self, chat_id: int) -> int:
        """Increment automatic retry count and return new value."""
        key = f"payment_retry_count:{chat_id}"
        try:
            count = self.redis.incr(key)
            self.redis.expire(key, 86400)  # 24h TTL
            return count
        except redis.RedisError as e:
            logger.error(f"Failed to increment retry count for chat_id={chat_id}: {e}")
            return 1

    def reset_retry_count(self, chat_id: int) -> None:
        """Reset automatic retry count to 0."""
        key = f"payment_retry_count:{chat_id}"
        try:
            self.redis.delete(key)
        except redis.RedisError as e:
            logger.error(f"Failed to reset retry count for chat_id={chat_id}: {e}")


