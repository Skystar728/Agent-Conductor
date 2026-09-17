"""User data models."""
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.reservation import TrainSearchParams


class UserCredentials:
    """Train account credentials."""

    def __init__(
        self,
        train_id: str = "",
        train_pw: str = "",
        korail_id: Optional[str] = None,
        korail_pw: Optional[str] = None
    ):
        self._train_id = train_id or korail_id or ""
        self._train_pw = train_pw or korail_pw or ""

    @property
    def train_id(self) -> str:
        return self._train_id

    @train_id.setter
    def train_id(self, val: str):
        self._train_id = val

    @property
    def korail_id(self) -> str:
        return self._train_id

    @korail_id.setter
    def korail_id(self, val: str):
        self._train_id = val

    @property
    def train_pw(self) -> str:
        return self._train_pw

    @train_pw.setter
    def train_pw(self, val: str):
        self._train_pw = val

    @property
    def korail_pw(self) -> str:
        return self._train_pw

    @korail_pw.setter
    def korail_pw(self, val: str):
        self._train_pw = val

    def __repr__(self):
        return f"UserCredentials(train_id={self._train_id!r}, train_pw='***', korail_id={self._train_id!r}, korail_pw='***')"

    def __eq__(self, other):
        if not isinstance(other, UserCredentials):
            return False
        return self.train_id == other.train_id and self.train_pw == other.train_pw


@dataclass
class UserSession:
    """User session data for conversation flow."""
    chat_id: int
    in_progress: bool = False
    last_action: int = 0  # Progress state (0-12)
    credentials: Optional[UserCredentials] = None
    train_info: dict = field(default_factory=dict)
    process_id: int = 9999999  # PID of background reservation process
    search_params: Optional['TrainSearchParams'] = None  # Search parameters for train reservation
    editing_field: Optional[str] = None  # Field currently being edited

    def reset(self) -> None:
        """Reset user session to initial state."""
        self.in_progress = False
        self.last_action = 0
        self.train_info = {}
        self.process_id = 9999999
        self.search_params = None
        self.editing_field = None


@dataclass
class UserProgress:
    """Represents user's progress in the reservation flow."""

    # Progress state constants
    INIT = 0
    STARTED = 1
    START_ACCEPTED = 2
    ID_INPUT_SUCCESS = 3
    PW_INPUT_SUCCESS = 4
    DATE_INPUT_SUCCESS = 5
    SRC_LOCATE_INPUT_SUCCESS = 6
    DST_LOCATE_INPUT_SUCCESS = 7
    DEP_TIME_INPUT_SUCCESS = 8
    MAX_DEP_TIME_INPUT_SUCCESS = 9
    TRAIN_TYPE_INPUT_SUCCESS = 10
    SPECIAL_INPUT_SUCCESS = 11
    PASSENGER_COUNT_INPUT_SUCCESS = 12  # New: passenger count selection
    SEAT_STRATEGY_INPUT_SUCCESS = 13  # New: seat allocation strategy
    FINDING_TICKET = 14  # Updated from 12 to 14
    EDIT_SELECT_FIELD = 15  # Selecting which option to edit
    EDIT_INPUT_VALUE = 16  # Inputting new value for selected option
    NATURAL_INPUT_CONFIRMATION = 17  # Natural language slot filling / confirmation state
