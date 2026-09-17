import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Mock external dependencies before importing application code if not installed
# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

from models import UserSession, UserProgress, TrainSearchParams, RunningReservation
from handlers.command_handler import CommandHandler
from handlers.conversation_handler import ConversationHandler
from services.reservation_service import ReservationService


class TestEditOptionFeature(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock()
        self.telegram = MagicMock()
        self.reservation = MagicMock()
        self.payment_reminder = MagicMock()

        self.cmd_handler = CommandHandler(
            storage=self.storage,
            telegram_service=self.telegram,
            reservation_service=self.reservation,
            payment_reminder_service=self.payment_reminder
        )
        self.conv_handler = ConversationHandler(
            storage=self.storage,
            telegram_service=self.telegram,
            reservation_service=self.reservation
        )

        self.chat_id = 123456
        self.sample_params = TrainSearchParams(
            dep_date="20260927",
            src_locate="울산(통도사)",
            dst_locate="서울",
            dep_time="193000",
            max_dep_time="2400",
            train_type="TrainType.KTX",
            train_type_display="KTX",
            special_option="ReserveOption.GENERAL_FIRST",
            special_option_display="GENERAL_FIRST",
            passenger_count=2,
            seat_strategy="random"
        )
        self.running_res = RunningReservation(
            chat_id=self.chat_id,
            process_id=50,
            korail_id="010-9629-7345",
            search_params=self.sample_params
        )
        self.session = UserSession(
            chat_id=self.chat_id,
            in_progress=True,
            last_action=UserProgress.FINDING_TICKET,
            process_id=50,
            search_params=self.sample_params
        )

    def test_handle_edit_shows_menu(self):
        self.storage.get_running_reservation.return_value = self.running_res
        self.storage.get_user_session.return_value = self.session

        self.cmd_handler.handle_edit(self.chat_id)

        self.assertEqual(self.session.last_action, UserProgress.EDIT_SELECT_FIELD)
        self.assertIsNone(self.session.editing_field)
        self.storage.save_user_session.assert_called_with(self.session)
        self.telegram.send_message.assert_called_once()
        sent_msg = self.telegram.send_message.call_args[0][1]
        self.assertIn("예약 옵션 수정", sent_msg)
        self.assertIn("좌석 배치 변경", sent_msg)

    def test_handle_seat_shortcut_changes_to_consecutive(self):
        self.storage.get_running_reservation.return_value = self.running_res

        self.cmd_handler.handle_seat(self.chat_id, "1")

        self.assertEqual(self.running_res.search_params.seat_strategy, "consecutive")
        self.reservation.restart_reservation.assert_called_once_with(
            self.chat_id, self.running_res.search_params
        )

    def test_handle_seat_shortcut_already_set(self):
        self.sample_params.seat_strategy = "consecutive"
        self.storage.get_running_reservation.return_value = self.running_res

        self.cmd_handler.handle_seat(self.chat_id, "1")

        self.reservation.restart_reservation.assert_not_called()
        self.telegram.send_message.assert_called_once()
        self.assertIn("이미 [연속 좌석] 방식으로 실행 중입니다", self.telegram.send_message.call_args[0][1])

    def test_edit_flow_select_seat_strategy_and_change_to_consecutive(self):
        self.storage.get_running_reservation.return_value = self.running_res
        self.session.last_action = UserProgress.EDIT_SELECT_FIELD

        # Step 1: User selects option 1 (seat strategy)
        self.conv_handler._handle_edit_select_field(self.chat_id, "1", self.session)
        self.assertEqual(self.session.editing_field, "seat_strategy")
        self.assertEqual(self.session.last_action, UserProgress.EDIT_INPUT_VALUE)

        # Step 2: User enters 1 for consecutive
        self.conv_handler._handle_edit_input_value(self.chat_id, "1", self.session)
        self.assertEqual(self.running_res.search_params.seat_strategy, "consecutive")
        self.reservation.restart_reservation.assert_called_once_with(
            self.chat_id, self.running_res.search_params
        )

    def test_edit_flow_cancel(self):
        self.storage.get_running_reservation.return_value = self.running_res
        self.session.last_action = UserProgress.EDIT_SELECT_FIELD

        # User enters 0 to cancel
        self.conv_handler._handle_edit_select_field(self.chat_id, "0", self.session)
        self.assertEqual(self.session.last_action, UserProgress.FINDING_TICKET)
        self.assertIsNone(self.session.editing_field)
        self.reservation.restart_reservation.assert_not_called()
        self.assertIn("옵션 수정을 취소했습니다", self.telegram.send_message.call_args[0][1])

    @patch('subprocess.Popen')
    @patch('os.kill')
    def test_reservation_service_restart_reservation(self, mock_os_kill, mock_popen):
        service = ReservationService(self.storage, self.telegram)
        mock_proc = MagicMock()
        mock_proc.pid = 99
        mock_popen.return_value = mock_proc

        self.session.credentials = MagicMock()
        self.session.credentials.korail_id = "010-1234-5678"
        self.session.credentials.korail_pw = "password123"
        self.storage.get_running_reservation.return_value = self.running_res
        self.storage.get_user_session.return_value = self.session

        new_params = TrainSearchParams(
            dep_date="20260927",
            src_locate="울산(통도사)",
            dst_locate="서울",
            dep_time="193000",
            max_dep_time="2400",
            train_type="TrainType.KTX",
            special_option="ReserveOption.GENERAL_FIRST",
            passenger_count=2,
            seat_strategy="consecutive"
        )

        res = service.restart_reservation(self.chat_id, new_params)
        self.assertTrue(res)
        mock_os_kill.assert_called_once()
        mock_popen.assert_called_once()
        self.assertEqual(self.session.process_id, 99)
        self.assertEqual(self.session.search_params.seat_strategy, "consecutive")
        self.telegram.send_message.assert_called_once()
        self.assertIn("연속 좌석", self.telegram.send_message.call_args[0][1])

    def test_in_flight_natural_language_edit_time(self):
        """Test in-flight modification of departure time via natural language."""
        self.storage.get_running_reservation.return_value = self.running_res
        self.storage.get_user_session.return_value = self.session
        self.session.train_info = {
            'depDate': '20260927',
            'srcLocate': '울산(통도사)',
            'dstLocate': '서울',
            'depTime': '193000',
            'maxDepTime': '2400',
            'trainType': 'TrainType.KTX',
            'trainTypeShow': 'KTX',
            'specialInfo': 'ReserveOption.GENERAL_FIRST',
            'specialInfoShow': 'GENERAL_FIRST',
            'passengerCount': 2,
            'seatStrategy': 'random',
            'seatStrategyShow': '랜덤 배치'
        }

        # User sends natural language modification
        self.conv_handler.handle_message(self.chat_id, "시간대 20시로 바꿔줘")

        # Verify restart_reservation was called with new time
        self.reservation.restart_reservation.assert_called_once()
        call_params = self.reservation.restart_reservation.call_args[0][1]
        self.assertEqual(call_params.dep_time, "200000")
        self.assertEqual(call_params.src_locate, "울산(통도사)")
        self.assertEqual(call_params.dst_locate, "서울")
        self.assertEqual(call_params.passenger_count, 2)

    def test_in_flight_natural_language_edit_passenger_count(self):
        """Test in-flight modification of passenger count via natural language."""
        self.storage.get_running_reservation.return_value = self.running_res
        self.storage.get_user_session.return_value = self.session
        self.session.train_info = {
            'depDate': '20260927',
            'srcLocate': '울산(통도사)',
            'dstLocate': '서울',
            'depTime': '193000',
            'maxDepTime': '2400',
            'trainType': 'TrainType.KTX',
            'trainTypeShow': 'KTX',
            'specialInfo': 'ReserveOption.GENERAL_FIRST',
            'specialInfoShow': 'GENERAL_FIRST',
            'passengerCount': 2,
            'seatStrategy': 'random',
            'seatStrategyShow': '랜덤 배치'
        }

        # User sends natural language change
        self.conv_handler.handle_message(self.chat_id, "3명으로 변경해줘")

        # Verify restart_reservation was called with updated passenger count
        self.reservation.restart_reservation.assert_called_once()
        call_params = self.reservation.restart_reservation.call_args[0][1]
        self.assertEqual(call_params.passenger_count, 3)

    def test_in_flight_natural_language_cancel(self):
        """Test in-flight cancellation via natural language."""
        self.storage.get_running_reservation.return_value = self.running_res
        self.storage.get_user_session.return_value = self.session

        self.conv_handler.handle_message(self.chat_id, "취소해줘")

        self.reservation.cancel_reservation.assert_called_once_with(self.chat_id)
        self.reservation.restart_reservation.assert_not_called()

    def test_in_flight_natural_language_no_change_shows_already_running(self):
        """Test in-flight text with no changes shows current status guide."""
        self.storage.get_running_reservation.return_value = self.running_res
        self.storage.get_user_session.return_value = self.session
        self.session.train_info = {
            'depDate': '20260927',
            'srcLocate': '울산(통도사)',
            'dstLocate': '서울',
            'depTime': '193000',
            'maxDepTime': '2400',
            'trainTypeShow': 'KTX',
            'specialInfoShow': 'GENERAL_FIRST'
        }

        self.conv_handler.handle_message(self.chat_id, "안녕하세요")

        self.reservation.restart_reservation.assert_not_called()
        self.telegram.send_message.assert_called_once()
        sent_msg = self.telegram.send_message.call_args[0][1]
        self.assertIn("진행", sent_msg)


if __name__ == '__main__':
    unittest.main()
