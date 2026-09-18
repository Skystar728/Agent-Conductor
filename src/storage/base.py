"""Base storage interface for application state management."""
from abc import ABC, abstractmethod
from typing import Optional, List

from models import UserSession, RunningReservation, PaymentStatus, MultiReservationStatus, TrainSearchParams


class StorageInterface(ABC):
    """Abstract interface for application state storage."""

    # User Session Management
    @abstractmethod
    def get_user_session(self, chat_id: int) -> Optional[UserSession]:
        """Get user session by chat ID."""
        pass

    @abstractmethod
    def save_user_session(self, session: UserSession) -> None:
        """Save or update user session."""
        pass

    @abstractmethod
    def delete_user_session(self, chat_id: int) -> None:
        """Delete user session."""
        pass

    @abstractmethod
    def get_all_user_sessions(self) -> List[UserSession]:
        """Get all user sessions."""
        pass

    @abstractmethod
    def clear_user_credentials(self, chat_id: int) -> None:
        """Clear user credentials from session storage immediately."""
        pass

    # Running Reservation Management
    @abstractmethod
    def get_running_reservation(self, chat_id: int) -> Optional[RunningReservation]:
        """Get running reservation by chat ID."""
        pass

    @abstractmethod
    def save_running_reservation(self, reservation: RunningReservation) -> None:
        """Save running reservation."""
        pass

    @abstractmethod
    def delete_running_reservation(self, chat_id: int) -> None:
        """Delete running reservation."""
        pass

    @abstractmethod
    def get_all_running_reservations(self) -> List[RunningReservation]:
        """Get all running reservations."""
        pass

    # Payment Status Management
    @abstractmethod
    def get_payment_status(self, chat_id: int) -> Optional[PaymentStatus]:
        """Get payment status by chat ID."""
        pass

    @abstractmethod
    def save_payment_status(self, status: PaymentStatus) -> None:
        """Save payment status."""
        pass

    @abstractmethod
    def delete_payment_status(self, chat_id: int) -> None:
        """Delete payment status."""
        pass

    # Subscriber Management
    @abstractmethod
    def add_subscriber(self, chat_id: int) -> None:
        """Add a subscriber for notifications."""
        pass

    @abstractmethod
    def remove_subscriber(self, chat_id: int) -> None:
        """Remove a subscriber."""
        pass

    @abstractmethod
    def get_all_subscribers(self) -> List[int]:
        """Get all subscriber chat IDs."""
        pass

    @abstractmethod
    def is_subscriber(self, chat_id: int) -> bool:
        """Check if chat ID is a subscriber."""
        pass

    # Admin Session Management
    @abstractmethod
    def is_admin_authenticated(self, chat_id: int) -> bool:
        """Check if chat ID is authenticated as admin."""
        pass

    @abstractmethod
    def set_admin_authenticated(self, chat_id: int, authenticated: bool = True) -> None:
        """Set admin authentication status for chat ID."""
        pass

    @abstractmethod
    def is_waiting_for_admin_password(self, chat_id: int) -> bool:
        """Check if user is waiting to enter admin password."""
        pass

    @abstractmethod
    def set_waiting_for_admin_password(self, chat_id: int, waiting: bool = True) -> None:
        """Set whether user is waiting to enter admin password."""
        pass

    @abstractmethod
    def get_pending_admin_command(self, chat_id: int) -> Optional[str]:
        """Get pending admin command waiting for authentication."""
        pass

    @abstractmethod
    def set_pending_admin_command(self, chat_id: int, command: Optional[str]) -> None:
        """Set pending admin command waiting for authentication."""
        pass

    # Multi-Reservation Status Management
    @abstractmethod
    def get_multi_reservation_status(self, chat_id: int) -> Optional[MultiReservationStatus]:
        """Get multi-reservation status by chat ID."""
        pass

    @abstractmethod
    def save_multi_reservation_status(self, status: MultiReservationStatus) -> None:
        """Save multi-reservation status."""
        pass

    @abstractmethod
    def delete_multi_reservation_status(self, chat_id: int) -> None:
        """Delete multi-reservation status."""
        pass

    @abstractmethod
    def get_all_multi_reservation_statuses(self) -> List[MultiReservationStatus]:
        """Get all multi-reservation statuses."""
        pass

    # Debug Mode Management
    @abstractmethod
    def is_debug_mode(self) -> bool:
        """Check if global debug mode is enabled."""
        pass

    @abstractmethod
    def set_debug_mode(self, enabled: bool) -> None:
        """Enable or disable global debug mode."""
        pass

    # AI Error Policy & HITL Management
    @abstractmethod
    def get_error_policy(self, error_hash: str) -> Optional[dict]:
        """Get cached error policy by error hash."""
        pass

    @abstractmethod
    def save_error_policy(self, error_hash: str, policy: dict, ttl: int = 604800) -> None:
        """Save error policy with TTL (default 7 days)."""
        pass

    @abstractmethod
    def is_user_notified(self, chat_id: int, error_hash: str) -> bool:
        """Check if user has already been notified of this error."""
        pass

    @abstractmethod
    def mark_user_notified(self, chat_id: int, error_hash: str, ttl: int = 3600) -> None:
        """Mark user as notified of this error to prevent spam (default 1 hour)."""
        pass

    @abstractmethod
    def set_hitl_request(self, chat_id: int, request_data: dict, ttl: int = 300) -> None:
        """Set active HITL decision request for user."""
        pass

    @abstractmethod
    def get_hitl_request(self, chat_id: int) -> Optional[dict]:
        """Get active HITL decision request for user."""
        pass

    @abstractmethod
    def delete_hitl_request(self, chat_id: int) -> None:
        """Delete active HITL request."""
        pass

    @abstractmethod
    def set_hitl_decision(self, chat_id: int, chosen_option: int) -> None:
        """Record user's decision for active HITL request."""
        pass

    @abstractmethod
    def get_hitl_decision(self, chat_id: int) -> Optional[int]:
        """Get user's recorded decision."""
        pass

    @abstractmethod
    def wait_for_hitl_decision(self, chat_id: int, timeout: int = 300, default_option: int = 1) -> int:
        """Wait for user's decision, falling back to default option on timeout."""
        pass

    # Last Search Params & Payment Retry Management
    @abstractmethod
    def save_last_search_params(self, chat_id: int, params: TrainSearchParams) -> None:
        """Save search parameters for possible restart on payment timeout."""
        pass

    @abstractmethod
    def get_last_search_params(self, chat_id: int) -> Optional[TrainSearchParams]:
        """Get saved search parameters."""
        pass

    @abstractmethod
    def delete_last_search_params(self, chat_id: int) -> None:
        """Delete saved search parameters."""
        pass

    @abstractmethod
    def get_retry_count(self, chat_id: int) -> int:
        """Get automatic retry count for payment timeout."""
        pass

    @abstractmethod
    def increment_retry_count(self, chat_id: int) -> int:
        """Increment automatic retry count and return new value."""
        pass

    @abstractmethod
    def reset_retry_count(self, chat_id: int) -> None:
        """Reset automatic retry count to 0."""
        pass

