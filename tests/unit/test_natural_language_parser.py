import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
import os
import sys
import json

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

from services.ai_agent_service import AIAgentService
from handlers.conversation_handler import ConversationHandler
from models import UserSession, UserProgress, UserCredentials
from telegramBot.messages import Messages


class TestNaturalLanguageReservation(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock()
        self.agent = AIAgentService(self.storage)
        self.telegram = MagicMock()
        self.reservation = MagicMock()
        self.conv_handler = ConversationHandler(
            self.storage,
            self.telegram,
            self.reservation,
            ai_agent_service=self.agent
        )
        # Fix KST time for deterministic tests: 2026-09-17 14:00 KST
        kst = timezone(timedelta(hours=9))
        self.now_kst = datetime(2026, 9, 17, 14, 0, tzinfo=kst)

    def test_parse_complete_natural_reservation(self):
        """Test parsing when LLM extracts all required slots."""
        mock_llm_json = {
            "is_reservation_intent": True,
            "src_locate": "서울",
            "dst_locate": "부산",
            "dep_date": "20260918",
            "dep_time": "1000",
            "passenger_count": 2,
            "train_type": "KTX",
            "reserve_option": "GENERAL_FIRST",
            "summary": "2026년 09월 18일 | 서울 ➔ 부산 | 10:00 이후 | 2명 | KTX"
        }

        with patch.object(self.agent, '_client') as mock_client:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(mock_llm_json)))]
            mock_client.chat.completions.create.return_value = mock_resp

            result = self.agent.parse_natural_reservation(
                "내일 오전 10시 서울에서 부산 KTX 2명",
                now_kst=self.now_kst
            )

            self.assertEqual(result["src_locate"], "서울")
            self.assertEqual(result["dst_locate"], "부산")
            self.assertEqual(result["dep_date"], "20260918")
            self.assertEqual(result["dep_time"], "1000")
            self.assertEqual(result["passenger_count"], 2)
            self.assertEqual(result["train_type"], "FLAGSHIP")
            self.assertEqual(result["missing_slots"], [])

    def test_parse_partial_slots_detects_missing(self):
        """Test partial inputs correctly identify missing slots."""
        mock_llm_json = {
            "is_reservation_intent": True,
            "src_locate": "동대구",
            "dst_locate": "서울",
            "dep_date": "20260920",
            "train_type": "KTX"
        }

        with patch.object(self.agent, '_client') as mock_client:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content=json.dumps(mock_llm_json)))]
            mock_client.chat.completions.create.return_value = mock_resp

            result = self.agent.parse_natural_reservation(
                "이번 주 일요일 동대구에서 서울 KTX",
                now_kst=self.now_kst
            )

            self.assertEqual(result["src_locate"], "동대구")
            self.assertEqual(result["dst_locate"], "서울")
            self.assertEqual(result["dep_date"], "20260920")
            self.assertIn("dep_time", result["missing_slots"])
            self.assertIn("passenger_count", result["missing_slots"])

    def test_station_name_resolution(self):
        """Test that station names like '부산역', '동대구역' are resolved to canonical names."""
        raw_slots = {
            "src_locate": "동대구역",
            "dst_locate": "부산역",
            "dep_date": "20260918",
            "dep_time": "1400",
            "passenger_count": 1
        }
        merged = self.agent._merge_and_validate_slots(raw_slots, None, "", self.now_kst)
        self.assertEqual(merged["src_locate"], "동대구")
        self.assertEqual(merged["dst_locate"], "부산")

    def test_heuristic_fallback_when_llm_fails(self):
        """Test heuristic extraction works even if LLM fails completely."""
        with patch.object(self.agent, '_client', None):
            result = self.agent.parse_natural_reservation(
                "내일 서울에서 부산 가고 싶어요",
                now_kst=self.now_kst
            )
            self.assertEqual(result["src_locate"], "서울")
            self.assertEqual(result["dst_locate"], "부산")
            self.assertEqual(result["dep_date"], "20260918")  # 2026-09-17 + 1 day

    def test_backward_compatibility_date_input_routes_to_legacy_step(self):
        """Entering 8-digit date at PW_INPUT_SUCCESS should route to legacy date input."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True, last_action=UserProgress.PW_INPUT_SUCCESS)
        self.storage.get_user_session.return_value = session
        self.storage.get_hitl_request.return_value = None

        with patch.object(self.conv_handler, '_handle_date_input') as mock_date, \
             patch.object(self.conv_handler, '_handle_natural_reservation_input') as mock_natural:
            self.conv_handler.handle_message(chat_id, "20260920")
            mock_date.assert_called_once_with(chat_id, "20260920", session)
            mock_natural.assert_not_called()

    def test_natural_input_at_pw_input_success_routes_to_natural_flow(self):
        """Entering natural text at PW_INPUT_SUCCESS should route to natural reservation flow."""
        chat_id = 12345
        session = UserSession(chat_id=chat_id, in_progress=True, last_action=UserProgress.PW_INPUT_SUCCESS)
        self.storage.get_user_session.return_value = session
        self.storage.get_hitl_request.return_value = None

        with patch.object(self.conv_handler, '_handle_date_input') as mock_date, \
             patch.object(self.conv_handler, '_handle_natural_reservation_input') as mock_natural:
            self.conv_handler.handle_message(chat_id, "내일 오전 10시 서울에서 부산 KTX 2명")
            mock_natural.assert_called_once_with(chat_id, "내일 오전 10시 서울에서 부산 KTX 2명", session)
            mock_date.assert_not_called()

    def test_natural_callback_sets_slot_and_updates_session(self):
        """Clicking inline buttons updates the slot and re-renders."""
        chat_id = 12345
        initial_slots = {
            "src_locate": "서울",
            "dst_locate": "부산",
            "dep_date": "20260918",
            "dep_time": "1000",
            "missing_slots": ["passenger_count"]
        }
        session = UserSession(
            chat_id=chat_id,
            in_progress=True,
            last_action=UserProgress.NATURAL_INPUT_CONFIRMATION,
            train_info={"nl_slots": initial_slots}
        )
        self.storage.get_user_session.return_value = session

        self.conv_handler.handle_natural_callback(chat_id, "NL_SET_passenger_count:2", session)

        # passenger_count should now be updated to 2
        updated_slots = session.train_info["nl_slots"]
        self.assertEqual(updated_slots["passenger_count"], 2)
        self.assertEqual(updated_slots["missing_slots"], [])
        self.assertEqual(session.train_info["passengerCount"], 2)

        # Telegram should have been called with confirmation card
        self.telegram.send_message.assert_called()

    def test_natural_callback_start_initiates_reservation(self):
        """Clicking NL_START when slots are complete starts the reservation."""
        chat_id = 12345
        complete_slots = {
            "src_locate": "서울",
            "dst_locate": "부산",
            "dep_date": "20260918",
            "dep_time": "1000",
            "passenger_count": 2,
            "train_type": "KTX",
            "reserve_option": "GENERAL_FIRST",
            "missing_slots": []
        }
        session = UserSession(
            chat_id=chat_id,
            in_progress=True,
            last_action=UserProgress.NATURAL_INPUT_CONFIRMATION,
            credentials=UserCredentials("010-1234-5678", "password123"),
            train_info={"nl_slots": complete_slots}
        )

        with patch.object(self.conv_handler, '_start_reservation') as mock_start:
            self.conv_handler.handle_natural_callback(chat_id, "NL_START", session)
            mock_start.assert_called_once_with(chat_id, session)

    def test_natural_callback_cancel_resets_session(self):
        """Clicking NL_CANCEL resets user session and sends cancellation notice."""
        chat_id = 12345
        session = UserSession(
            chat_id=chat_id,
            in_progress=True,
            last_action=UserProgress.NATURAL_INPUT_CONFIRMATION,
            train_info={"nl_slots": {"src_locate": "서울"}}
        )

        self.conv_handler.handle_natural_callback(chat_id, "NL_CANCEL", session)
        self.assertFalse(session.in_progress)
        self.assertEqual(session.last_action, 0)
        self.assertEqual(self.telegram.send_message.call_args[0], (chat_id, Messages.CANCELLED_BY_USER))


if __name__ == '__main__':
    unittest.main()
