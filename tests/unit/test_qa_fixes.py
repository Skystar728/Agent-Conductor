import pytest
from unittest.mock import MagicMock, patch
from flask import Flask
from flask_restful import Api

from models.user import UserProgress, UserSession, UserCredentials
from handlers.conversation_handler import ConversationHandler
from handlers.command_handler import CommandHandler
from api.telegram_webhook import TelegramWebhook


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.get_user_session.return_value = None
    storage.is_user_notified.return_value = False
    return storage


@pytest.fixture
def mock_telegram():
    tg = MagicMock()
    return tg


@pytest.fixture
def mock_reservation():
    return MagicMock()


@pytest.fixture
def mock_payment_reminder():
    return MagicMock()


@pytest.fixture
def conv_handler(mock_storage, mock_telegram, mock_reservation):
    return ConversationHandler(mock_storage, mock_telegram, mock_reservation)


@pytest.fixture
def cmd_handler(mock_storage, mock_telegram, mock_reservation, mock_payment_reminder):
    return CommandHandler(mock_storage, mock_telegram, mock_reservation, mock_payment_reminder)


class TestQAFixes:
    def test_password_input_login_failure_retry_routing(self, conv_handler, mock_telegram, mock_storage):
        """Issue 1: Test Y/N routing when login failed and user responds."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True)
        session.last_action = UserProgress.ID_INPUT_SUCCESS  # user was prompted to retry
        mock_storage.get_user_session.return_value = session

        # Test retry Y
        conv_handler.handle_message(chat_id, "Y")
        assert session.last_action == UserProgress.START_ACCEPTED
        mock_telegram.send_message.assert_called()

        # Test cancel N
        session.last_action = UserProgress.ID_INPUT_SUCCESS
        conv_handler.handle_message(chat_id, "N")
        assert session.last_action == 0
        assert not session.in_progress

    def test_password_login_failure_sends_keyboard(self, conv_handler, mock_telegram, mock_storage):
        """Issue 1: Login failure sends inline keyboard buttons."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True)
        session.last_action = UserProgress.ID_INPUT_SUCCESS
        session.credentials = UserCredentials(korail_id="01012345678", korail_pw="")
        mock_storage.get_user_session.return_value = session

        with patch("handlers.conversation_handler.KorailService.login", return_value=False):
            conv_handler.handle_message(chat_id, "wrongpass123")
            assert session.last_action == UserProgress.ID_INPUT_SUCCESS
            # Verify reply_markup was passed to send_message
            _, kwargs = mock_telegram.send_message.call_args
            assert "reply_markup" in kwargs
            assert kwargs["reply_markup"] is not None

    def test_dst_station_cannot_be_same_as_src(self, conv_handler, mock_telegram, mock_storage):
        """Issue 7: Destination cannot be identical to departure station."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True)
        session.last_action = UserProgress.SRC_LOCATE_INPUT_SUCCESS
        session.train_info['srcLocate'] = "서울"
        mock_storage.get_user_session.return_value = session

        conv_handler.handle_message(chat_id, "서울")
        # Should stay on SRC_LOCATE_INPUT_SUCCESS (waiting for dst station)
        assert session.last_action == UserProgress.SRC_LOCATE_INPUT_SUCCESS
        call_text = mock_telegram.send_message.call_args[0][1]
        assert "동일할 수 없습니다" in call_text

    def test_max_dep_time_validation(self, conv_handler, mock_telegram, mock_storage):
        """Issue 8: max_dep_time cannot be earlier than dep_time, but 2400 is allowed."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True)
        session.last_action = UserProgress.DEP_TIME_INPUT_SUCCESS
        session.train_info['depTime'] = "140000"
        mock_storage.get_user_session.return_value = session

        # Earlier time should be rejected
        conv_handler.handle_message(chat_id, "1200")
        assert session.last_action == UserProgress.DEP_TIME_INPUT_SUCCESS
        call_text = mock_telegram.send_message.call_args[0][1]
        assert "보다 이후여야 합니다" in call_text

        # Later time should be accepted
        conv_handler.handle_message(chat_id, "1800")
        assert session.last_action == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        assert session.train_info['maxDepTime'] == "1800"

        # 2400 should be accepted even if dep_time is late
        session.last_action = UserProgress.DEP_TIME_INPUT_SUCCESS
        session.train_info['depTime'] = "220000"
        conv_handler.handle_message(chat_id, "2400")
        assert session.last_action == UserProgress.MAX_DEP_TIME_INPUT_SUCCESS
        assert session.train_info['maxDepTime'] == "2400"

    def test_seat_command_transitions_state(self, cmd_handler, mock_telegram, mock_storage):
        """Issue 5: /seat without args sets EDIT_INPUT_VALUE state so buttons work."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True)
        session.last_action = UserProgress.FINDING_TICKET
        mock_storage.get_user_session.return_value = session
        mock_storage.get_running_reservation.return_value = MagicMock(search_params=MagicMock())

        cmd_handler.handle_seat(chat_id, "")
        assert session.last_action == UserProgress.EDIT_INPUT_VALUE
        assert session.editing_field == "seat_strategy"

    def test_edit_time_allows_2400_and_validates_range(self, conv_handler, mock_telegram, mock_storage):
        """Issue 4: /edit dep_time supports 2400 and rejects reversed ranges."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True)
        session.last_action = UserProgress.EDIT_INPUT_VALUE
        session.editing_field = "dep_time"
        mock_storage.get_user_session.return_value = session

        params = MagicMock()
        mock_storage.get_running_reservation.return_value = MagicMock(search_params=params)

        # Reversed range should fail
        conv_handler.handle_message(chat_id, "1800-1400")
        call_text = mock_telegram.send_message.call_args[0][1]
        assert "보다 이후여야 합니다" in call_text

        # Valid range with 2400 should succeed
        conv_handler.handle_message(chat_id, "1400-2400")
        assert params.dep_time == "140000"
        assert params.max_dep_time == "2400"

    def test_passenger_count_1_sets_single_seat(self, conv_handler, mock_telegram, mock_storage):
        """Issue 9: 1 passenger shows '단독 좌석' instead of '1명'."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True)
        session.last_action = UserProgress.SPECIAL_INPUT_SUCCESS
        session.train_info = {
            'depDate': '20261010',
            'srcLocate': '서울',
            'dstLocate': '부산',
            'depTime': '100000',
            'maxDepTime': '2400',
            'trainType': 'TrainType.KTX',
            'trainTypeShow': 'KTX',
            'specialInfo': 'ReserveOption.GENERAL_FIRST',
            'specialInfoShow': '일반실 우선',
        }
        mock_storage.get_user_session.return_value = session

        conv_handler.handle_message(chat_id, "1")
        assert session.train_info.get('seatStrategyShow') == "단독 좌석"

    def test_webhook_status_1_resets_session_and_running(self, mock_storage, mock_telegram):
        """Issue 3: Webhook status=1 cleans up running_reservation and resets session."""
        app = Flask(__name__)
        api = Api(app)
        api.add_resource(
            TelegramWebhook,
            '/telebot',
            resource_class_kwargs={
                'storage': mock_storage,
                'telegram_service': mock_telegram,
                'reservation_service': MagicMock(),
                'payment_reminder_service': MagicMock()
            }
        )
        client = app.test_client()

        session = UserSession(chat_id=12345)
        session.last_action = UserProgress.FINDING_TICKET
        session.in_progress = True
        mock_storage.get_user_session.return_value = session

        response = client.get(
            '/telebot',
            query_string={
                'chatId': '12345',
                'msg': '예약 실패 메시지',
                'status': '1'
            }
        )
        assert response.status_code == 200
        # Verify session was reset
        assert session.last_action == 0
        assert not session.in_progress
        mock_storage.delete_running_reservation.assert_called_with(12345)
