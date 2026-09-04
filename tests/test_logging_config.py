import logging

from jobsniffer.logging_config import create_logger, set_logger_level


def test_create_logger_uses_jobspy_prefixed_name():
    logger = create_logger("Widget")
    assert logger.name == "JobSpy:Widget"


def test_create_logger_does_not_propagate_to_root():
    logger = create_logger("NoPropagate")
    assert logger.propagate is False


def test_create_logger_is_idempotent_about_handlers():
    first = create_logger("Idempotent")
    second = create_logger("Idempotent")
    assert first is second
    assert len(first.handlers) == 1


def test_set_logger_level_none_is_a_no_op():
    logger = create_logger("NoneVerbose")
    logger.setLevel(logging.INFO)
    set_logger_level(None)
    assert logger.level == logging.INFO


def test_set_logger_level_2_sets_info():
    logger = create_logger("VerboseTwo")
    logger.setLevel(logging.ERROR)
    set_logger_level(2)
    assert logger.level == logging.INFO


def test_set_logger_level_1_sets_warning():
    logger = create_logger("VerboseOne")
    logger.setLevel(logging.INFO)
    set_logger_level(1)
    assert logger.level == logging.WARNING


def test_set_logger_level_0_sets_error():
    logger = create_logger("VerboseZero")
    logger.setLevel(logging.INFO)
    set_logger_level(0)
    assert logger.level == logging.ERROR


def test_set_logger_level_only_affects_jobspy_prefixed_loggers():
    unrelated = logging.getLogger("SomeOtherLibrary")
    unrelated.setLevel(logging.INFO)
    set_logger_level(0)
    assert unrelated.level == logging.INFO
