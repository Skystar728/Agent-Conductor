from unittest.mock import Mock

from requests.exceptions import ConnectionError

from config.settings import settings
from services.korail_service import KorailService


class Http403Error(Exception):
    status_code = 403


class UnknownKorailError(RuntimeError):
    pass


class BlockedKorail:
    def __init__(self) -> None:
        self.logout_calls = 0

    def search_train(self, *_args, **_kwargs):
        raise Http403Error("code: -2000")

    def logout(self) -> None:
        self.logout_calls += 1


class UnknownErrorKorail:
    def search_train(self, *_args, **_kwargs):
        raise UnknownKorailError("새로운 코레일 오류")


class LogoutFailureKorail:
    def logout(self) -> None:
        raise ConnectionError("logout unavailable")


class Http429Error(Exception):
    status_code = 429


class CongestedThenHealthyKorail:
    def __init__(self) -> None:
        self.calls = 0

    def search_train(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise Http429Error("Too Many Requests")
        return []


def logged_in_service(korail_instance) -> KorailService:
    service = KorailService()
    service._korail_instance = korail_instance
    service._logged_in = True
    service._username = "test-user"
    service._password = "test-password"
    return service


def search(service: KorailService) -> list:
    return service.search_trains("20260920", "서울", "부산", verbose=False)


def test_normal_sleep_uses_configured_jitter(monkeypatch) -> None:
    service = KorailService()
    uniform = Mock(return_value=4.25)
    monkeypatch.setattr("services.korail_service.random.uniform", uniform)

    assert service.get_sleep_interval() == 4.25
    uniform.assert_called_once_with(
        settings.KORAIL_SEARCH_MIN_INTERVAL,
        settings.KORAIL_SEARCH_MAX_INTERVAL,
    )


def test_second_403_logs_out_and_clears_local_session() -> None:
    korail = BlockedKorail()
    service = logged_in_service(korail)

    assert search(service) == []
    assert service.get_sleep_interval() == settings.WAF_COOLDOWN_SECONDS
    assert service.is_logged_in is True

    assert search(service) == []
    assert korail.logout_calls == 1
    assert service.is_waf_max_exceeded() is True
    assert service.is_logged_in is False
    assert service._korail_instance is None
    assert service._username is None
    assert service._password is None
    assert "-2000" in service.last_waf_error


def test_unknown_error_is_forwarded_to_llm_listener() -> None:
    service = logged_in_service(UnknownErrorKorail())
    listener = Mock()
    service.error_listener = listener

    assert search(service) == []
    listener.assert_called_once_with("UnknownKorailError: 새로운 코레일 오류")


def test_429_cooldown_returns_to_jitter_after_success(monkeypatch) -> None:
    service = logged_in_service(CongestedThenHealthyKorail())
    monkeypatch.setattr("services.korail_service.random.uniform", lambda *_args: 4.25)

    assert search(service) == []
    assert service.get_sleep_interval() == settings.RATE_LIMIT_COOLDOWN_SECONDS

    assert search(service) == []
    assert service.get_sleep_interval() == 4.25


def test_logout_clears_local_session_when_remote_logout_fails() -> None:
    service = logged_in_service(LogoutFailureKorail())

    service.logout()

    assert service.is_logged_in is False
    assert service._korail_instance is None
    assert service._username is None
    assert service._password is None


class MaintenanceKorail:
    def __init__(self) -> None:
        self.maintenance = True

    def search_train(self, *_args, **_kwargs):
        if self.maintenance:
            raise RuntimeError("보다 편리한 서비스를 제공하기 위해 서비스를 일시중지하오니 양해해 주시기 바랍니다. (S000)")
        return ["KTX 101"]

    def login(self) -> bool:
        return not self.maintenance


def test_maintenance_error_enters_standby_and_recovers(monkeypatch) -> None:
    korail = MaintenanceKorail()
    service = logged_in_service(korail)
    listener = Mock()
    service.maintenance_listener = listener

    monkeypatch.setattr("services.korail_service.random.uniform", lambda a, b: 240.0)

    # 1. First search encounters maintenance error
    assert search(service) == []
    assert service.is_in_maintenance() is True
    assert service.get_sleep_interval() == 240.0
    listener.assert_called_once()
    assert listener.call_args[0][0] is True  # is_active = True
    assert "S000" in listener.call_args[0][1] or "일시중지" in listener.call_args[0][1]

    # 2. While in maintenance and login fails, it remains in standby
    service._logged_in = False
    monkeypatch.setattr(service, "_relogin", Mock(return_value=False))
    assert search(service) == []
    assert service.is_in_maintenance() is True
    assert service.get_sleep_interval() == 240.0

    # 3. Server finishes maintenance - relogin succeeds and search resumes
    korail.maintenance = False
    monkeypatch.setattr(service, "_relogin", Mock(side_effect=lambda: setattr(service, '_logged_in', True) or True))
    monkeypatch.setattr("services.korail_service.random.uniform", lambda a, b: 4.25)

    trains = search(service)
    assert trains == ["KTX 101"]
    assert service.is_in_maintenance() is False
    assert service.get_sleep_interval() == 4.25

