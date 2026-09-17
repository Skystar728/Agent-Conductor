"""Regression tests for the background reservation process parser."""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch


def _load_background_process_module():
    """Load the parser with the lightweight dependency shapes used in production."""
    korail2 = types.ModuleType("korail2")

    class StringTrainType:
        KTX = "KTX"
        ALL = "ALL"

    class StringReserveOption:
        GENERAL_FIRST = "GENERAL_FIRST"
        GENERAL_ONLY = "GENERAL_ONLY"
        SPECIAL_FIRST = "SPECIAL_FIRST"
        SPECIAL_ONLY = "SPECIAL_ONLY"

    korail2.TrainType = StringTrainType
    korail2.ReserveOption = StringReserveOption

    services = types.ModuleType("services")
    for name in (
        "TrainService",
        "KorailService",
        "TelegramService",
        "PaymentReminderService",
        "MultiReservationReminderService",
        "AIAgentService",
    ):
        setattr(services, name, type(name, (), {}))

    korail_service = types.ModuleType("services.korail_service")
    korail_service.DuplicateReservationError = type(
        "DuplicateReservationError", (Exception,), {}
    )

    storage = types.ModuleType("storage")
    storage.__path__ = []
    storage_redis = types.ModuleType("storage.redis")
    storage_redis.RedisStorage = type("RedisStorage", (), {})

    utils = types.ModuleType("utils")
    utils.__path__ = []
    utils_logger = types.ModuleType("utils.logger")
    utils_logger.get_logger = lambda name: types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
    )
    utils_logger.LoggerFactory = type(
        "LoggerFactory", (), {"set_log_level": staticmethod(lambda level: None)}
    )

    stubs = {
        "korail2": korail2,
        "services": services,
        "services.korail_service": korail_service,
        "storage": storage,
        "storage.redis": storage_redis,
        "utils": utils,
        "utils.logger": utils_logger,
    }
    module_name = "telebotBackProcess_regression"
    module_path = Path(__file__).parents[2] / "src/telegramBot/telebotBackProcess.py"

    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load background process module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module


def _new_process():
    """Create a process instance without starting external services."""
    module = _load_background_process_module()
    return module.BackgroundReservationProcess.__new__(
        module.BackgroundReservationProcess
    )


def test_parse_reserve_option_accepts_serialized_string_values():
    """String-valued ReserveOption members must parse without AttributeError."""
    process = _new_process()
    cases = {
        "GENERAL_FIRST": "GENERAL_FIRST",
        "ReserveOption.GENERAL_ONLY": "GENERAL_ONLY",
        "SPECIAL_FIRST": "SPECIAL_FIRST",
        "reserveoption.special_only": "SPECIAL_ONLY",
    }

    for raw_value, expected in cases.items():
        assert process._parse_reserve_option(raw_value) == expected


def test_parse_reserve_option_defaults_unknown_values():
    """Unknown reserve-option strings retain the general-first default."""
    process = _new_process()
    assert process._parse_reserve_option("unknown-option") == "GENERAL_FIRST"
