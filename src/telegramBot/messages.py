"""
메시지 템플릿 관리 모듈.

모든 시스템 안내 메시지를 중앙에서 관리하여 일관성과 유지보수성을 향상시킵니다.
"""
from config.settings import settings


class Messages:
    """메시지 템플릿 클래스"""

    # ========== 시작 및 안내 메시지 ==========
    WELCOME = """🚂 Agent-Conductor에 오신 것을 환영합니다!

지능형 철도 여정 오케스트레이터입니다.
열차 좌석을 탐색하고 확보하여 안전한 여정을 조율합니다.
좌석 확보 완료 시 결제는 안내 기한 내에 직접 진행해주셔야 합니다.

📋 여정 정보 조율 순서
━━━━━━━━━━━━━━━━
  1. 철도 로그인 계정 확인
  2. 출발 희망일
  3. 출발역 및 도착역
  4. 시간대 및 좌석 조건
━━━━━━━━━━━━━━━━

계속 진행하시려면 "예" 또는 "Y"를 입력해주세요.
"""

    HELP = """🚂 Agent-Conductor 사용 안내

🎫 여정 조율
  /start - 여정 조율 시작
  /cancel - 진행 중인 여정 취소
  /edit - 진행 중인 옵션(시간대/좌석) 수정
  /seat [1|2] - 좌석 배치 즉시 변경 (1:연속, 2:랜덤)

ℹ️ 정보 및 설정
  /status - 현재 여정 상태 확인
  /help - 도움말 보기

💡 결제 알림은 좌석 확보 후 아무 메시지나 입력하면 중단됩니다.
"""

    HELP_ADMIN = """🔧 관리자 명령어 (인증 필요)
  /subscribe - 알림 구독
  /allusers - 전체 사용자 확인
  /cancelall - 전체 예약 취소
  /broadcast [메시지] - 공지사항 전송
  /flushredis - Redis 메모리 초기화 (⚠️ 위험)
  /debug_on - 상세 디버그 로그 활성화
  /debug_off - 디버그 로그 비활성화
"""

    # ========== 로그인 관련 메시지 ==========
    REQUEST_PHONE = """📱 코레일 로그인 정보 입력을 시작합니다.

현재 휴대폰 번호 로그인만 지원됩니다.

휴대전화번호를 입력해 주세요.
예시: 010-1234-5678

⚠️ 하이픈(-)을 반드시 포함하여 입력해주세요.
💡 취소를 원하시면 /cancel을 입력하세요.
"""

    REQUEST_PASSWORD = """✅ 아이디 입력 완료

🔒 비밀번호를 입력해주세요.
"""

    LOGIN_SUCCESS = """✅ 로그인 성공!

💬 원하시는 열차 예약을 편하게 한 문장으로 말씀해주세요!
예시:
• "내일 오전 10시 서울에서 부산 KTX 2명"
• "이번주 토요일 15시 이후 동대구-서울"
• "9월 25일 저녁 광명에서 대전 KTX"

💡 기존 방식대로 출발 희망일 8자리(예: 20260920)를 입력하셔도 됩니다.
"""

    LOGIN_FAILED_RETRY = """❌ 로그인 실패

입력하신 정보:
━━━━━━━━━━━━━━
아이디: {username}
비밀번호: 보안상 비공개
━━━━━━━━━━━━━━

다음 중 하나를 선택해주세요:
  • Y 또는 예 → 계정정보 다시 입력
  • N 또는 아니오 → 작업 취소
  • 비밀번호만 다시 입력 → 같은 아이디로 재시도

⚠️ 주의: 5회 이상 로그인 실패 시 코레일 홈페이지에서 비밀번호를 재설정해야 합니다.
"""

    # ========== 예약 정보 입력 메시지 ==========
    REQUEST_DATE = """✅ 출발일 입력 완료

🚉 출발역을 입력해주세요.
예시: 광명, 서울, 부산 등

💡 역 이름만 입력 ('역' 제외)
📍 역 목록: https://www.korail.com/ticket/train/stationGuide/station
"""

    REQUEST_SRC_STATION = """✅ 출발역 입력 완료

🏁 도착역을 입력해주세요.
예시: 광주송정, 대전, 동대구 등

💡 역 이름만 입력 ('역' 제외)
📍 역 목록: https://www.korail.com/ticket/train/stationGuide/station
"""

    REQUEST_DST_STATION = """✅ 도착역 입력 완료

🕐 검색 시작 시각을 입력해주세요.

형식: HHMM (24시간 기준, 4자리)
예시: 1305 (오후 1시 5분 이후 열차 검색)
"""

    REQUEST_DEP_TIME = """✅ 검색 시작 시각 입력 완료

🕐 검색 종료 시각을 입력해주세요.

형식: HHMM (24시간 기준, 4자리)
예시: 1800 (오후 6시까지의 열차만 검색)

💡 시간 제한 없이 검색하려면 2400 입력 (권장)
"""

    REQUEST_TRAIN_TYPE = """✅ 시간 입력 완료

🚄 열차 종류를 선택해주세요.

1️⃣ Flagship Train (플래그십 - KTX 등 고속열차)
2️⃣ Entry Train (엔트리 - 일반/전체 열차 포함)

숫자를 입력하세요: 1 또는 2
"""

    REQUEST_SEAT_TYPE = """✅ 열차 종류 선택 완료

💺 좌석 종류를 선택해주세요.

1️⃣ 일반실 우선
2️⃣ 일반실만
3️⃣ 특실 우선
4️⃣ 특실만

숫자를 입력하세요: 1, 2, 3, 4
"""

    REQUEST_PASSENGER_COUNT = """✅ 좌석 종류 선택 완료

👥 탑승 인원수를 입력해주세요.

💡 1~9명까지 선택 가능합니다.
(현재는 성인 인원수만 지원합니다)

예) 2명이 탑승하는 경우: 2
"""

    REQUEST_SEAT_STRATEGY = """✅ 인원수 입력 완료 (총 {count}명)

🪑 좌석 배치 방식을 선택해 주십시오.

━━━━━━━━━━━━━━━━━━━━
1️⃣ 연속 좌석 (권장)
   • 같이 앉을 수 있도록 연속된 좌석 예약
   • 연속된 좌석이 없으면 예약 실패

2️⃣ 랜덤 배치
   • 한 자리씩 개별적으로 예약
   • 좌석이 떨어져 있을 수 있음
   • 예약 성공률이 더 높음
━━━━━━━━━━━━━━━━━━━━

숫자를 입력하세요: 1 또는 2
"""

    CONFIRM_RESERVATION = """✅ 모든 정보 입력 완료!

📋 예약 정보 확인
━━━━━━━━━━━━━━━━━━━━
📅 출발일: {depDate}
🚉 출발역: {srcLocate}
🏁 도착역: {dstLocate}
🕐 검색 시작: {depTime}
⏰ 검색 종료: {maxDepTime}
🚄 열차: {trainTypeShow}
💺 좌석: {specialInfoShow}
👥 인원: {passengerCount}명
🪑 배치: {seatStrategy}
━━━━━━━━━━━━━━━━━━━━

• Y 또는 예 → 예약 시작
• N 또는 아니오 → 작업 취소

⏱ 예약 완료까지 시간이 걸릴 수 있습니다.
"""

    RESERVATION_STARTED = """🎯 예약 검색을 시작합니다!

🔍 매진된 자리에 공석이 생길 때까지 계속 확인합니다.
✅ 예약 성공 시 즉시 알려드립니다!

💡 진행 중인 예약을 취소하려면 /cancel을 입력하세요.
"""

    ALREADY_RUNNING = """⚠️ 이미 예약이 진행 중입니다.

📋 진행 중인 예약 정보
━━━━━━━━━━━━━━━━━━━━
📅 출발일: {depDate}
🚉 출발역: {srcLocate}
🏁 도착역: {dstLocate}
🕐 검색 시작: {depTime}
🚄 열차: {trainTypeShow}
💺 좌석: {specialInfoShow}
━━━━━━━━━━━━━━━━━━━━
💡 시간대나 인원 변경은 '19시로 변경', '2명으로 변경'처럼 바로 말씀해주세요.
💡 옵션 메뉴 직접 열기는 /edit, 예약을 취소하려면 /cancel을 입력하세요.
"""

    # ========== 옵션 수정 관련 메시지 ==========
    EDIT_MENU = """⚙️ 예약 옵션 수정

현재 설정된 예약 정보:
━━━━━━━━━━━━━━━━━━━━
📅 출발일: {depDate}
🚉 구간: {srcLocate} ➡️ {dstLocate}
🕐 검색 시간: {depTime} ~ {maxDepTime}
🚄 열차 종류: {trainTypeShow}
💺 좌석 종류: {specialInfoShow}
👥 탑승 인원: {passengerCount}명
🪑 좌석 배치: {seatStrategyShow}
━━━━━━━━━━━━━━━━━━━━

수정할 항목 번호를 입력해주세요:
1️⃣ 좌석 배치 변경 (연속 좌석 ↔ 랜덤 배치)
2️⃣ 검색 시간대 변경 (시작시각/최대시각)
3️⃣ 열차 종류 변경 (Flagship / Entry)
4️⃣ 좌석 종류 변경 (일반실 / 특실)
5️⃣ 탑승 인원수 변경 (1~9명)
0️⃣ 수정 취소 (기존 예약 유지)
"""

    EDIT_NO_RUNNING_RESERVATION = "⚠️ 현재 진행 중인 여정이 없습니다.\n먼저 /start로 여정 조율을 시작해주세요."
    EDIT_CANCELLED = "✅ 옵션 수정을 취소했습니다. 기존 Agent-Conductor 여정 탐색을 계속 유지합니다."
    EDIT_INVALID_CHOICE = "❌ 0~5 사이의 번호를 입력해주세요."

    # ========== 에러 메시지 ==========
    ERROR_GENERIC = "⚠️ 오류가 발생했습니다.\n/cancel 또는 /start로 다시 시작해주세요."
    ERROR_INVALID_COMMAND = "❌ 알 수 없는 명령어입니다.\n/help로 사용 가능한 명령어를 확인하세요."
    ERROR_NO_PROGRESS = "ℹ️ 진행 중인 예약이 없습니다.\n/start를 입력하여 예약을 시작하세요."
    ERROR_PHONE_FORMAT = "❌ 전화번호 형식이 올바르지 않습니다.\n하이픈(-)을 포함하여 다시 입력해주세요.\n예시: 010-1234-5678"
    ERROR_DATE_FORMAT = """❌ 날짜 형식이 올바르지 않습니다.

8자리 숫자로 입력해주세요.
예시: 20250425 (2025년 4월 25일)

⚠️ 과거 날짜는 입력할 수 없습니다.
"""
    ERROR_TIME_FORMAT = "❌ 시간 형식이 올바르지 않습니다.\nHHMM 형식 4자리로 입력해주세요.\n예시: 1430 (오후 2시 30분)"
    ERROR_TRAIN_TYPE_INVALID = "❌ 1 또는 2를 입력해주세요."
    ERROR_SEAT_TYPE_INVALID = "❌ 1, 2, 3, 4 중 하나를 입력해주세요."
    ERROR_PASSENGER_COUNT_NOT_DIGIT = "❌ 숫자를 입력해주세요. (1~9)"
    ERROR_PASSENGER_COUNT_RANGE = "❌ 1~9명 사이의 인원수를 입력해주세요."
    ERROR_SEAT_STRATEGY_INVALID = "❌ 1 또는 2를 입력해주세요."
    ERROR_CONFIRM_INVALID = """❌ 올바른 응답을 입력해주세요.

• Y 또는 예 → 예약 시작
• N 또는 아니오 → 작업 취소
"""
    ERROR_ADMIN_ENV = "⚠️ 서버 환경변수가 설정되지 않았습니다."
    ERROR_ADMIN_LOGIN = "⚠️ 관리자 계정 로그인에 실패했습니다."
    ERROR_MASTER_LOGIN_UNAUTHORIZED = """🚫 마스터 로그인 권한이 없습니다.

마스터 로그인은 등록된 특정 관리자만 사용할 수 있습니다.
일반 로그인을 진행하시려면 /start 를 입력해주세요."""
    ERROR_RESERVATION_START_FAILED = "❌ 예약 프로세스 시작에 실패했습니다.\n다시 시도해주세요."
    ERROR_NOT_SUBSCRIBER = """⚠️ 구독이 필요한 서비스입니다.

2024년부터 본 서비스가 유료화되었습니다.
구독을 원하시면 관리자에게 문의해주세요.

예약을 취소합니다.
"""
    # ========== 취소 및 완료 메시지 ==========
    CANCELLED = "✅ 예약이 취소되었습니다."
    CANCELLED_BY_USER = "🚫 예약을 취소합니다."
    CANCEL_START_CONFIRMATION = "🚫 예약 진행을 취소합니다."

    PAYMENT_REMINDER_STOPPED = """✅ 결제 리마인더가 중단되었습니다.

결제를 완료하셨다면 즐거운 여행 되세요! 🚄
아직 결제하지 않으셨다면 서둘러 결제를 완료해주세요.
"""

    PAYMENT_REMINDER_TIMEOUT = f"""⏰ 결제 리마인더 종료

예약 후 10분이 경과하여 리마인더가 자동 종료되었습니다.
결제를 완료하지 않으셨다면 예약이 취소되었을 수 있습니다.

💡 공식 사이트에서 예약 상태를 확인해주세요.
🔗 {settings.TRAIN_PAYMENT_URL}
"""

    # ========== 관리자 메시지 ==========
    ADMIN_AUTH_REQUIRED = "🔐 관리자 인증이 필요합니다.\n관리자 비밀번호를 입력해주세요."
    ADMIN_AUTH_SUCCESS = "✅ 관리자 인증 성공!"
    ADMIN_AUTH_FAILED = "❌ 관리자 인증 실패\n올바른 비밀번호를 입력해주세요."

    # ========== Backward Compatibility Methods for MessageTemplates ==========
    # These methods provide compatibility with the old MessageTemplates interface

    @staticmethod
    def welcome_message():
        """Welcome message (compatibility method)"""
        return Messages.WELCOME

    @staticmethod
    def request_phone_number():
        """Request phone number (compatibility method)"""
        return Messages.REQUEST_PHONE

    @staticmethod
    def request_password():
        """Request password (compatibility method)"""
        return Messages.REQUEST_PASSWORD

    @staticmethod
    def login_success():
        """Login success (compatibility method)"""
        return Messages.LOGIN_SUCCESS

    @staticmethod
    def login_failure(username: str):
        """Login failure (compatibility method)"""
        return Messages.LOGIN_FAILED_RETRY.format(username=username)

    @staticmethod
    def request_departure_station():
        """Request departure station after date input (compatibility method)"""
        return Messages.REQUEST_DATE

    @staticmethod
    def request_arrival_station():
        """Request arrival station after departure station input (compatibility method)"""
        return Messages.REQUEST_SRC_STATION

    @staticmethod
    def not_in_allow_list():
        """Not in allow list (compatibility method)"""
        return Messages.ERROR_NOT_SUBSCRIBER

    @staticmethod
    def reservation_started():
        """Reservation started (compatibility method)"""
        return Messages.RESERVATION_STARTED

    @staticmethod
    def reservation_cancelled():
        """Reservation cancelled (compatibility method)"""
        return Messages.CANCELLED

    @staticmethod
    def help_message():
        """Help message (compatibility method)"""
        return Messages.HELP

    @staticmethod
    def payment_reminder(remaining_minutes: int, remaining_seconds: int):
        """Payment reminder (compatibility method)"""
        from config.settings import settings

        if remaining_seconds == 0:
            time_text = f"{remaining_minutes}분"
        else:
            time_text = f"{remaining_minutes}분 {remaining_seconds}초"

        return f"""⏰ 결제 리마인더

예약 취소까지 남은 시간: {time_text}

📱 스마트폰: '코레일톡' 앱 ➡️ 하단 [승차권 확인/장바구니]
🌐 웹 결제: {settings.KORAIL_PAYMENT_URL}

서둘러 결제를 완료해주세요!
💡 결제 완료 후 아무 메시지나 입력하면 알림이 중단됩니다.
"""

    @staticmethod
    def natural_reservation_summary(params: dict) -> str:
        """Format confirmation card for natural language reservation."""
        d = params.get("dep_date", "")
        formatted_date = f"{d[:4]}년 {d[4:6]}월 {d[6:8]}일" if len(d) == 8 else (d or "미지정")
        t = params.get("dep_time", "")
        formatted_time = f"{t[:2]}:{t[2:]} 이후" if len(t) == 4 else (t or "00:00 (첫차부터)")
        max_t = params.get("max_dep_time", "2400")
        max_str = f" (~ {max_t[:2]}:{max_t[2:]} 이전)" if max_t and max_t != "2400" else ""
        passengers = f"{params.get('passenger_count', 1)}명"
        tt_val = str(params.get("train_type", "")).upper()
        train_type = "Flagship (플래그십)" if any(k in tt_val for k in ("FLAGSHIP", "KTX")) else "Entry (엔트리/전체)"
        seat_opt = "일반실 우선" if params.get("reserve_option") == "GENERAL_FIRST" else "지정 옵션"
        strategy = "동반석/연속석" if str(params.get("seat_strategy", "1")) == "1" else "개별석/산재예약"

        return f"""📋 [예약 정보 확인]

• 구간: {params.get('src_locate', '미지정')} ➔ {params.get('dst_locate', '미지정')}
• 일시: {formatted_date} {formatted_time}{max_str}
• 인원: 성인 {passengers}
• 열차: {train_type} ({seat_opt}, {strategy})

━━━━━━━━━━━━━━━━━━━━
위 정보로 열차 조회를 시작할까요?"""

    @staticmethod
    def slot_filling_request(params: dict, missing_slots: list) -> str:
        """Prompt user for missing slots with current progress."""
        lines = ["🤖 [AI 비서 정보 확인]"]
        if params.get("summary"):
            lines.append(f"\n현재 확인된 내용:\n👉 {params['summary']}")

        missing_names = []
        if "src_locate" in missing_slots:
            missing_names.append("출발역")
        if "dst_locate" in missing_slots:
            missing_names.append("도착역")
        if "dep_date" in missing_slots:
            missing_names.append("출발 날짜")
        if "dep_time" in missing_slots:
            missing_names.append("출발 시간")
        if "passenger_count" in missing_slots:
            missing_names.append("탑승 인원")

        if missing_names:
            lines.append(f"\n💡 아래 버튼을 눌러 '{', '.join(missing_names)}' 항목을 선택하시거나 추가로 말씀해 주세요!")
        else:
            lines.append("\n아래 버튼을 선택해 주세요.")

        return "\n".join(lines)



