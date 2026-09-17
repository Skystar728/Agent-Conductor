import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import json

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

from services.ai_agent_service import AIAgentService, AgentPlan
from handlers.conversation_handler import ConversationHandler
from models import UserSession, UserProgress


class TestAIAgentService(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock()
        self.agent = AIAgentService(self.storage)

    def test_compute_error_hash_normalization(self):
        err1 = "2026년 추석 특별수송기간  승차권\n예약 일시 변경"
        err2 = "2026년   추석 특별수송기간 승차권 예약 일시 변경"
        self.assertEqual(
            AIAgentService.compute_error_hash(err1),
            AIAgentService.compute_error_hash(err2)
        )

    def test_sanitize_error_redacts_credentials_and_phone_numbers(self):
        raw = 'password="secret" Authorization: Bearer abc.def 010-1234-5678'
        safe = AIAgentService.sanitize_error_message(raw)

        self.assertNotIn('secret', safe)
        self.assertNotIn('abc.def', safe)
        self.assertNotIn('010-1234-5678', safe)
        self.assertIn('[REDACTED]', safe)
        self.assertIn('[REDACTED_PHONE]', safe)

    def test_cache_hit_returns_without_llm_call(self):
        err_msg = "추석 특별수송기간 안내"
        err_hash = AIAgentService.compute_error_hash(err_msg)

        cached_dict = {
            "error_hash": err_hash,
            "raw_error": err_msg,
            "reason": "추석 특별수송기간 차단",
            "user_notice": "추석 기간으로 60초 대기합니다.",
            "requires_hitl": False,
            "autonomous_action": "SET_INTERVAL",
            "autonomous_params": {"seconds": 60.0},
            "hitl_question": None,
            "hitl_options": [],
            "hitl_default_option": 1,
            "hitl_timeout_seconds": 300
        }
        self.storage.get_error_policy.return_value = cached_dict

        with patch.object(self.agent, '_call_llm_agent') as mock_call:
            plan = self.agent.analyze_error(err_msg)
            mock_call.assert_not_called()
            self.assertEqual(plan.error_hash, err_hash)
            self.assertEqual(plan.reason, "추석 특별수송기간 차단")
            self.assertFalse(plan.requires_hitl)

    def test_cache_miss_calls_llm_and_saves_to_redis_only_on_success(self):
        err_msg = "새로운 안내 메시지"
        err_hash = AIAgentService.compute_error_hash(err_msg)
        self.storage.get_error_policy.return_value = None

        mock_plan = AgentPlan(
            error_hash=err_hash,
            raw_error=err_msg,
            reason="새로운 안내",
            user_notice="상황 보고입니다.",
            requires_hitl=True,
            hitl_question="어떻게 할까요?",
            hitl_options=[{"id": 1, "title": "1분 대기", "action": "SET_INTERVAL", "params": {"seconds": 60}}],
            hitl_default_option=1,
            hitl_timeout_seconds=300
        )

        # 1. Success case: Should save to Redis
        with patch.object(self.agent, '_call_llm_agent', return_value=(mock_plan, True)) as mock_call:
            plan = self.agent.analyze_error(err_msg)
            mock_call.assert_called_once_with(err_hash, err_msg)
            self.storage.save_error_policy.assert_called_once()
            self.assertTrue(plan.requires_hitl)
            self.assertEqual(plan.reason, "새로운 안내")

        # 2. Failure case: Should NOT save to Redis
        self.storage.save_error_policy.reset_mock()
        with patch.object(self.agent, '_call_llm_agent', return_value=(mock_plan, False)):
            self.agent.analyze_error("다른 에러")
            self.storage.save_error_policy.assert_not_called()

    def test_fallback_plan_when_no_api_key(self):
        err_msg = "임의의 오류 메시지"
        err_hash = AIAgentService.compute_error_hash(err_msg)
        self.agent._client = None
        plan, is_success = self.agent._call_llm_agent(err_hash, err_msg)
        self.assertFalse(is_success)
        self.assertFalse(plan.requires_hitl)
        self.assertEqual(plan.autonomous_action, "SET_INTERVAL")
        self.assertEqual(plan.autonomous_params["seconds"], 60.0)



class TestHITLConversationIntegration(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock()
        self.telegram = MagicMock()
        self.reservation = MagicMock()
        self.conv_handler = ConversationHandler(
            storage=self.storage,
            telegram_service=self.telegram,
            reservation_service=self.reservation
        )
        self.chat_id = 7895770150

    def test_hitl_input_intercepted_and_recorded(self):
        # Setup active HITL request in storage
        self.storage.get_hitl_request.return_value = {
            "error_hash": "abc1234",
            "question": "어떻게 진행할까요?",
            "options": [
                {"id": 1, "title": "60초 대기", "action": "SET_INTERVAL"},
                {"id": 2, "title": "예약 중단", "action": "STOP"}
            ],
            "default_option": 1
        }
        self.storage.get_user_session.return_value = UserSession(
            chat_id=self.chat_id,
            last_action=UserProgress.FINDING_TICKET
        )

        # User sends "2"
        self.conv_handler.handle_message(self.chat_id, "2")

        # Verify choice recorded and confirmation message sent
        self.storage.set_hitl_decision.assert_called_once_with(self.chat_id, 2)
        self.telegram.send_message.assert_called_once()
        sent_text = self.telegram.send_message.call_args[0][1]
        self.assertIn("예약 중단", sent_text)
        self.assertIn("선택되었습니다", sent_text)


if __name__ == '__main__':
    unittest.main()
