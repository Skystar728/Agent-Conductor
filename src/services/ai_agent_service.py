"""
AI Agent Service for analyzing Korail errors and executing HITL (Human-in-the-Loop) plans.
Powered by NVIDIA Build API (moonshotai/kimi-k3).
"""
import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from config.settings import settings
from storage.base import StorageInterface
from utils.logger import get_logger
from utils.station_codes import resolve_station_name

logger = get_logger(__name__)


@dataclass
class HitlOption:
    """Option for user decision in Human-in-the-loop flow."""
    id: int
    title: str
    action: str  # "SET_INTERVAL", "SLEEP_UNTIL", "STOP", "CONTINUE"
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentPlan:
    """Action plan produced by the AI Agent."""
    error_hash: str
    raw_error: str
    reason: str
    user_notice: str
    requires_hitl: bool = False
    autonomous_action: Optional[str] = "SET_INTERVAL"
    autonomous_params: Dict[str, Any] = field(default_factory=lambda: {"seconds": 60.0})
    hitl_question: Optional[str] = None
    hitl_options: List[Dict[str, Any]] = field(default_factory=list)
    hitl_default_option: int = 1
    hitl_timeout_seconds: int = 300

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentPlan":
        return cls(**data)


class AIAgentService:
    """Service that leverages NVIDIA Build API (Kimi-k3) to analyze errors and plan actions."""

    SYSTEM_PROMPT = """당신은 철도 여정 오케스트레이터 Agent-Conductor 시스템의 지능형 AI 에이전트 어시스턴트입니다.
철도 API 통신 중 발생한 오류나 안내 메시지를 심층 분석하여, Agent-Conductor가 취해야 할 최적의 행동 플랜(Action Plan)을 수립해야 합니다.

[판단 기준]
1. 특정 점검 시간 / 오픈 예정 시각이 명시된 경우 (예: "01:00 ~ 04:30 점검", "07:00부터 발매 개시", "10시 오픈" 등):
   - autonomous_action을 "SLEEP_UNTIL"로 설정하고, autonomous_params에 {"target_time": "HH:MM"} (24시간 KST 기준, 예: "04:30", "07:00")를 지정합니다.
   - 불필요하게 1분마다 서버를 호출하지 않고, 해당 시각까지 안전하게 슬립 대기한 뒤 정각에 조회를 자동 재개합니다.
   - user_notice에 "철도 시스템 점검(또는 발매 오픈) 예정 시각인 HH:MM까지 대기 후 자동으로 조회를 재개합니다." 문구를 포함합니다.
2. 단순 에러 / 일시 차단 / 특별수송기간 안내 등 특정 시각이 명시되지 않은 경우:
   - 트래픽 제한을 방지하기 위해 탐색 주기를 60초로 완화(SET_INTERVAL, {"seconds": 60.0})합니다.
   - user_notice에 정중한 상황 설명 및 60초 대기 안내 문구를 작성합니다.
3. 사용자 선택(Human-In-The-Loop) 필요 여부 (requires_hitl):
   - 사용자가 직접 대기 vs 중단 vs 시간조정 등을 결정해야 할 가치가 있는 경우 requires_hitl=true 로 설정하고 2~3개의 명확한 선택지(hitl_options)를 제시합니다.
   - 단순 일시적 오류나 명확한 시스템 점검/백오프는 requires_hitl=false 로 자율 처리합니다.

[출력 형식 - 반드시 순수 JSON만 출력 (Markdown 코드 블록 없이)]:
{
  "reason": "한 줄의 에러 핵심 원인",
  "user_notice": "사용자에게 보낼 정중한 상황 설명 및 향후 계획 문구",
  "requires_hitl": false,
  "autonomous_action": "SET_INTERVAL",
  "autonomous_params": { "seconds": 60.0 },
  "hitl_question": "선택이 필요한 경우 사용자에게 던질 질문",
  "hitl_options": [
    { "id": 1, "title": "차단 방지 모드로 60초마다 계속 확인", "action": "SET_INTERVAL", "params": { "seconds": 60.0 } },
    { "id": 2, "title": "Conductor 여정 탐색 중단", "action": "STOP", "params": {} }
  ],
  "hitl_default_option": 1,
  "hitl_timeout_seconds": 300
}
    """
    _SECRET_PATTERN = re.compile(
        r'''(?ix)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|user[_-]?pw|bottoken|secret|cookie|session[_-]?id)\b["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)'''
    )
    _BEARER_PATTERN = re.compile(r'''(?i)(\bBearer\s+)[^\s,;"'}]+''')
    _PHONE_PATTERN = re.compile(r'(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)')

    def __init__(self, storage: StorageInterface):
        self.storage = storage
        self._client = None
        self._init_client()

    def _init_client(self):
        if settings.AI_GATEWAY in ('none', 'off', 'false', '') or not settings.LLM_API_KEY:
            logger.info("AI Gateway is disabled (AI_GATEWAY=none). Operating in deterministic safety fallback mode.")
            return

        try:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                timeout=60.0,
                max_retries=1
            )
            logger.info(f"AIAgentService initialized with model '{settings.LLM_MODEL}' via {settings.LLM_BASE_URL}")
        except ImportError:
            logger.error("openai package not installed. Please install openai>=1.0.0.")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")

    @staticmethod
    def compute_error_hash(error_message: str) -> str:
        """Create a normalized MD5 hash of the error message."""
        normalized = " ".join(error_message.strip().split())
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def sanitize_error_message(cls, raw_error: str) -> str:
        text = str(raw_error or '')
        text = cls._BEARER_PATTERN.sub(r'\1[REDACTED]', text)
        text = cls._SECRET_PATTERN.sub(r'\1[REDACTED]', text)
        text = cls._PHONE_PATTERN.sub('[REDACTED_PHONE]', text)
        return text[:4000]

    def analyze_error(self, raw_error: str) -> AgentPlan:
        """
        Analyze an error message using cached Redis policy or NVIDIA Kimi-k3 API.
        """
        safe_error = self.sanitize_error_message(raw_error)
        error_hash = self.compute_error_hash(safe_error)

        # 1. Check Redis Cache
        cached_policy = self.storage.get_error_policy(error_hash)
        if cached_policy:
            logger.info(f"⚡ [Cache Hit] Loaded error policy for hash {error_hash} from Redis")
            try:
                cached_policy["raw_error"] = safe_error
                return AgentPlan.from_dict(cached_policy)
            except Exception as e:
                logger.error(f"Failed to build AgentPlan from cache: {e}")

        # 2. Cache Miss - Call LLM API
        logger.info(f"🧠 [Cache Miss] Calling AI model ({settings.LLM_MODEL}) for error hash {error_hash}...")
        plan, is_success = self._call_llm_agent(error_hash, safe_error)

        # 3. Save to Redis ONLY if LLM call succeeded!
        if is_success:
            try:
                self.storage.save_error_policy(error_hash, plan.to_dict())
                logger.info(f"✅ Successfully cached LLM error policy for {error_hash}")
            except Exception as e:
                logger.error(f"Failed to cache error policy: {e}")
        else:
            logger.warning(f"⚠️ LLM call failed or fell back - skipping Redis caching for {error_hash}")

        return plan

    @staticmethod
    def _clean_json_content(content: str) -> str:
        """Strip markdown fences, think tags, and extract JSON substring."""
        if not content:
            return ""
        content = content.strip()
        if "</think>" in content:
            content = content.split("</think>")[-1].strip()
        if "```" in content:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            else:
                content = content.split("```")[1].split("```")[0].strip()
        elif "{" in content and "}" in content:
            start_idx = content.find("{")
            end_idx = content.rfind("}") + 1
            content = content[start_idx:end_idx]
        return content

    def _call_llm_agent(self, error_hash: str, raw_error: str) -> tuple[AgentPlan, bool]:
        """Query LLM API to analyze error. Returns (AgentPlan, is_success)."""
        if not self._client or not settings.LLM_API_KEY:
            logger.info("AI gateway API key not configured. Using intelligent fallback policy.")
            return self._build_fallback_plan(error_hash, raw_error), False

        models_to_try = [settings.LLM_MODEL] if settings.LLM_MODEL else []
        safe_error = self.sanitize_error_message(raw_error)

        for model_name in models_to_try:
            try:
                logger.info(f"Invoking AI model '{model_name}' for error analysis...")
                response = self._client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": f"코레일에서 발생한 오류/안내 메시지:\n\"{safe_error}\""}
                    ],
                    temperature=0.2,
                    max_tokens=1500,
                    timeout=45.0,
                )

                content = response.choices[0].message.content
                if not content:
                    logger.warning(f"Empty content received from model {model_name}")
                    continue

                parsed = json.loads(self._clean_json_content(content))

                plan = AgentPlan(
                    error_hash=error_hash,
                    raw_error=safe_error,
                    reason=parsed.get("reason", "코레일 안내 응답 수신"),
                    user_notice=parsed.get("user_notice", f"코레일 응답: {safe_error[:100]}..."),
                    requires_hitl=bool(parsed.get("requires_hitl", False)),
                    autonomous_action=parsed.get("autonomous_action") or "SET_INTERVAL",
                    autonomous_params=parsed.get("autonomous_params") or {"seconds": 60.0},
                    hitl_question=parsed.get("hitl_question"),
                    hitl_options=parsed.get("hitl_options") or [],
                    hitl_default_option=int(parsed.get("hitl_default_option") or 1),
                    hitl_timeout_seconds=int(parsed.get("hitl_timeout_seconds") or 300)
                )
                logger.info(f"✅ AI analysis succeeded using '{model_name}'")
                return plan, True

            except Exception as e:
                logger.warning(f"Model '{model_name}' failed: {e}. Trying next fallback model if available...")

        return self._build_fallback_plan(error_hash, raw_error), False

    def _build_fallback_plan(self, error_hash: str, raw_error: str) -> AgentPlan:
        """Reliable fallback plan when LLM is unreachable or unconfigured."""
        raw_error = self.sanitize_error_message(raw_error)
        return AgentPlan(
            error_hash=error_hash,
            raw_error=raw_error,
            reason="코레일 안내 응답 (자동 완화 모드)",
            user_notice=f"코레일 시스템 응답이 감지되어 차단 방지를 위해 60초 간격으로 대기 모드를 유지합니다.\n\n내용: {raw_error}",
            requires_hitl=False,
            autonomous_action="SET_INTERVAL",
            autonomous_params={"seconds": 60.0},
            hitl_question=None,
            hitl_options=[],
            hitl_default_option=1,
            hitl_timeout_seconds=300
        )

    NATURAL_RESERVATION_SYSTEM_PROMPT = """당신은 철도 열차 여정 조율 어시스턴트 AI입니다.
사용자의 자연어 요청 문장에서 열차 좌석 확보에 필요한 정보를 정확히 추출해야 합니다.

[현재 기준 시각 (KST)]:
{current_time_str}

[파싱 규칙]:
1. 출발역 (src_locate) & 도착역 (dst_locate):
   - '역' 접미사는 제외한 역 이름 (예: "서울역" -> "서울", "부산역" -> "부산", "동대구역" -> "동대구", "대전역" -> "대전" 등)
   - 언급이 없으면 null
2. 출발 희망일 (dep_date):
   - 반드시 "YYYYMMDD" 8자리 숫자 문자열 (예: "20260918")
   - 상대적 날짜("오늘", "내일", "모레", "이번주 금요일", "다음주 일요일", "9월 25일" 등)는 현재 기준 시각을 바탕으로 정확히 계산하여 8자리로 출력
   - 언급이 없으면 null
3. 출발 희망 시각 (dep_time):
   - "HHMM" 4자리 숫자 문자열 (예: "0900", "1430", "1800")
   - "오전 10시" -> "1000", "오후 2시" -> "1400", "아침" -> "0800", "낮/점심" -> "1200", "저녁/퇴근" -> "1800", "밤" -> "2100"
   - 언급이 없으면 null
4. 최대 출발 시각 (max_dep_time):
   - "HHMM" 4자리 (예: "18시 이전" -> "1800", 언급 없으면 "2400")
5. 열차 종류 및 등급 (train_type):
   - "FLAGSHIP" (플래그십: KTX 등 고속열차, 기본값) 또는 "ENTRY" (엔트리: 새마을/무궁화 포함 전체 열차)
6. 탑승 인원 (passenger_count):
   - 성인 인원수 정수 (예: 1, 2, 3 등. "혼자/나" -> 1, "친구랑 둘이" -> 2, 언급 없으면 null)
7. 좌석 옵션 (reserve_option):
   - "GENERAL_FIRST" (일반실 우선, 기본값), "GENERAL_ONLY", "SPECIAL_FIRST", "SPECIAL_ONLY"
8. 좌석 전략 (seat_strategy):
   - "1" (연속/동반석, 기본값), "2" (개별석)
9. is_reservation_intent:
   - 열차 좌석 예약 및 탑승과 관련된 의도이면 true, 단순 잡담이나 무관한 내용이면 false.

[출력 형식 - 반드시 순수 JSON만 출력 (Markdown 코드 블록 ``` 없이)]:
{{
  "is_reservation_intent": true,
  "src_locate": "서울",
  "dst_locate": "부산",
  "dep_date": "20260918",
  "dep_time": "1000",
  "max_dep_time": "2400",
  "train_type": "FLAGSHIP",
  "reserve_option": "GENERAL_FIRST",
  "passenger_count": 2,
  "seat_strategy": "1",
  "summary": "9월 18일 서울 ➔ 부산 Flagship 2명"
}}
"""

    def parse_natural_reservation(
        self,
        user_text: str,
        current_params: Optional[dict] = None,
        now_kst: Optional[datetime] = None
    ) -> dict:
        """
        Extract train reservation parameters from natural language input.
        Returns a dictionary containing extracted slot values, missing slots, and summary.
        """
        if now_kst is None:
            kst = timezone(timedelta(hours=9))
            now_kst = datetime.now(kst)

        current_time_str = now_kst.strftime("%Y년 %m월 %d일 %A %H:%M KST")
        prompt = self.NATURAL_RESERVATION_SYSTEM_PROMPT.format(current_time_str=current_time_str)

        context_prompt = ""
        if current_params:
            clean_current = {k: v for k, v in current_params.items() if v is not None}
            if clean_current:
                context_prompt = f"\n[이전에 사용자가 입력하여 이미 확인된 정보]:\n{json.dumps(clean_current, ensure_ascii=False)}\n"

        extracted = {}
        if self._client and settings.LLM_API_KEY:
            try:
                response = self._client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": f"{context_prompt}사용자 입력 문장:\n\"{user_text}\""}
                    ],
                    temperature=0.1,
                    max_tokens=800,
                    timeout=15.0,
                )
                content = response.choices[0].message.content or ""
                extracted = json.loads(self._clean_json_content(content))
            except Exception as e:
                logger.warning(f"Failed to parse natural reservation via LLM: {e}. Using heuristic fallback.")

        return self._merge_and_validate_slots(extracted, current_params, user_text, now_kst)

    def _merge_and_validate_slots(
        self,
        extracted: dict,
        current_params: Optional[dict],
        user_text: str,
        now_kst: datetime
    ) -> dict:
        """Merge LLM extraction with previous parameters and validate slots."""
        merged = dict(current_params or {})

        # Merge extracted fields if present
        for key in ("src_locate", "dst_locate", "dep_date", "dep_time", "max_dep_time",
                    "train_type", "reserve_option", "passenger_count", "seat_strategy"):
            val = extracted.get(key)
            if val is not None and val != "":
                # If current_params already has option and user did not mention it, preserve current_params
                if current_params and current_params.get(key) is not None:
                    if key == "seat_strategy" and not any(k in user_text for k in ("랜덤", "1석씩", "개별", "따로", "연속", "붙어서", "함께", "나란히")):
                        continue
                    if key == "train_type" and not any(k in user_text for k in ("KTX", "새마을", "무궁화", "전체", "모든")):
                        continue
                    if key == "reserve_option" and not any(k in user_text for k in ("특실", "우등", "일반")):
                        continue
                merged[key] = val

        # 1. Station names extraction and validation
        stations = [
            "광주송정", "천안아산", "여수EXPO", "동대구", "서대전", "신경주",
            "서울", "용산", "광명", "수원", "대전", "부산", "수서", "포항",
            "강릉", "전주", "익산", "순천", "목포", "울산", "마산", "진주", "오송"
        ]
        found_stations = [s for s in stations if s in user_text]
        if len(found_stations) >= 2:
            idx1 = user_text.find(found_stations[0])
            idx2 = user_text.find(found_stations[1])
            if idx1 < idx2:
                merged["src_locate"] = found_stations[0]
                merged["dst_locate"] = found_stations[1]
            else:
                merged["src_locate"] = found_stations[1]
                merged["dst_locate"] = found_stations[0]
        elif len(found_stations) == 1:
            s = found_stations[0]
            if any(marker in user_text for marker in ("에서", "출발")):
                merged["src_locate"] = s
            elif any(marker in user_text for marker in ("으로", "로", "도착", "행", "까지")):
                merged["dst_locate"] = s
            elif not merged.get("src_locate"):
                merged["src_locate"] = s
            elif not merged.get("dst_locate"):
                merged["dst_locate"] = s

        if merged.get("src_locate"):
            src_str = str(merged["src_locate"]).strip()
            if src_str.endswith("역") and len(src_str) > 1:
                src_str = src_str[:-1]
            resolved = resolve_station_name(src_str)
            merged["src_locate"] = resolved or src_str

        if merged.get("dst_locate"):
            dst_str = str(merged["dst_locate"]).strip()
            if dst_str.endswith("역") and len(dst_str) > 1:
                dst_str = dst_str[:-1]
            resolved = resolve_station_name(dst_str)
            merged["dst_locate"] = resolved or dst_str

        # 2. Validate date format (YYYYMMDD) or relative dates
        if "오늘" in user_text:
            merged["dep_date"] = now_kst.strftime("%Y%m%d")
        elif "내일" in user_text:
            merged["dep_date"] = (now_kst + timedelta(days=1)).strftime("%Y%m%d")
        elif "모레" in user_text:
            merged["dep_date"] = (now_kst + timedelta(days=2)).strftime("%Y%m%d")
        else:
            m_kdate = re.search(r'(\d{1,2})월\s*(\d{1,2})일', user_text)
            m_numdate = re.search(r'\b(202\d{5})\b', user_text)
            if m_kdate:
                m_val = int(m_kdate.group(1))
                d_val = int(m_kdate.group(2))
                y_val = now_kst.year
                if m_val < now_kst.month:
                    y_val += 1
                merged["dep_date"] = f"{y_val}{m_val:02d}{d_val:02d}"
            elif m_numdate:
                merged["dep_date"] = m_numdate.group(1)
            elif merged.get("dep_date"):
                date_str = str(merged["dep_date"]).replace("-", "").replace(".", "").strip()
                if len(date_str) == 8 and date_str.isdigit():
                    merged["dep_date"] = date_str
                else:
                    merged["dep_date"] = None

        # 3. Validate / extract passenger count
        m_pass = re.search(r'(?:인원\s*[:=]?\s*([1-9])|([1-9])\s*(?:명|인|좌석|자리))', user_text)
        if m_pass:
            c = m_pass.group(1) or m_pass.group(2)
            merged["passenger_count"] = int(c)
        elif merged.get("passenger_count") is not None:
            try:
                merged["passenger_count"] = int(merged["passenger_count"])
                if not (1 <= merged["passenger_count"] <= 9):
                    merged["passenger_count"] = 1
            except (ValueError, TypeError):
                merged["passenger_count"] = 1

        # 4. Extract time ranges or start times
        m_time_range = re.search(r'(\d{1,2})\s*(?:시|:)?(?:\d{2})?\s*(?:~|-|부터)\s*(\d{1,2})\s*(?:시|:)?(?:\d{2})?', user_text)
        if m_time_range:
            h1 = int(m_time_range.group(1))
            h2 = int(m_time_range.group(2))
            merged["dep_time"] = f"{h1:02d}00"
            merged["max_dep_time"] = f"{h2:02d}00"
        else:
            m_colon = re.search(r'(오전|오후|저녁|아침|밤)?\s*(\d{1,2}):(\d{2})', user_text)
            m_si = re.search(r'(오전|오후|저녁|아침|밤)?\s*(\d{1,2})\s*시', user_text)
            if m_colon:
                period, h_str, m_str = m_colon.groups()
                h = int(h_str)
                if period in ("오후", "저녁", "밤") and h < 12:
                    h += 12
                elif period in ("오전", "아침") and h == 12:
                    h = 0
                merged["dep_time"] = f"{h:02d}{m_str}"
            elif m_si:
                period, h_str = m_si.groups()
                h = int(h_str)
                if period in ("오후", "저녁", "밤") and h < 12:
                    h += 12
                elif period in ("오전", "아침") and h == 12:
                    h = 0
                merged["dep_time"] = f"{h:02d}00"

        # 5. Extract options (train type, seat strategy, seat reserve option)
        if any(k in user_text for k in ("랜덤", "1석씩", "개별", "따로")):
            merged["seat_strategy"] = "2"
        elif any(k in user_text for k in ("연속", "붙어서", "함께")):
            merged["seat_strategy"] = "1"

        if any(k in user_text for k in ("특실", "우등")):
            merged["reserve_option"] = "SPECIAL_FIRST"
        elif any(k in user_text for k in ("일반실", "일반")):
            merged["reserve_option"] = "GENERAL_FIRST"

        if any(k in user_text for k in ("전체 열차", "모든 열차", "새마을", "무궁화", "엔트리", "entry", "일반열차")):
            merged["train_type"] = "ENTRY"
        elif any(k in user_text.upper() for k in ("FLAGSHIP", "플래그십", "고속열차", "KTX")):
            merged["train_type"] = "FLAGSHIP"

        # Normalize train_type
        if merged.get("train_type"):
            tt_u = str(merged["train_type"]).upper()
            if any(k in tt_u for k in ("FLAGSHIP", "KTX")):
                merged["train_type"] = "FLAGSHIP"
            elif any(k in tt_u for k in ("ENTRY", "STANDARD", "ALL")):
                merged["train_type"] = "ENTRY"

        # 6. Defaults for optional options
        if not merged.get("train_type"):
            merged["train_type"] = "FLAGSHIP"
        if not merged.get("reserve_option"):
            merged["reserve_option"] = "GENERAL_FIRST"
        if not merged.get("seat_strategy"):
            merged["seat_strategy"] = "1"
        if not merged.get("max_dep_time"):
            merged["max_dep_time"] = "2400"

        # Determine missing slots
        missing = []
        if not merged.get("src_locate"):
            missing.append("src_locate")
        if not merged.get("dst_locate"):
            missing.append("dst_locate")
        if not merged.get("dep_date"):
            missing.append("dep_date")
        if not merged.get("dep_time"):
            missing.append("dep_time")
        if merged.get("passenger_count") is None:
            missing.append("passenger_count")

        merged["missing_slots"] = missing
        merged["is_reservation_intent"] = extracted.get("is_reservation_intent", True)
        if "summary" in extracted and extracted["summary"]:
            merged["summary"] = extracted["summary"]
        else:
            merged["summary"] = self._build_natural_summary(merged)

        return merged

    def _build_natural_summary(self, params: dict) -> str:
        """Build human-friendly Korean summary of parsed reservation."""
        parts = []
        if params.get("dep_date"):
            d = params["dep_date"]
            parts.append(f"{d[:4]}년 {d[4:6]}월 {d[6:8]}일")
        if params.get("src_locate") and params.get("dst_locate"):
            parts.append(f"{params['src_locate']} ➔ {params['dst_locate']}")
        elif params.get("src_locate"):
            parts.append(f"{params['src_locate']} 출발")
        elif params.get("dst_locate"):
            parts.append(f"{params['dst_locate']} 도착")

        tt = params.get('train_type', 'FLAGSHIP')
        tt_show = "Flagship" if tt in ("FLAGSHIP", "KTX") else "Entry"
        parts.append(tt_show)

        if params.get("dep_time"):
            t = params["dep_time"]
            parts.append(f"{t[:2]}:{t[2:]} 이후")
        if params.get("passenger_count"):
            parts.append(f"{params['passenger_count']}명")

        return " | ".join(parts) if parts else "열차 여정 정보"

