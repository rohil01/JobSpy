"""jobsniffer.logging_config

Logger creation, extracted from jobsniffer.util into its own leaf module.
util.py imports jobsniffer.http (for CurlCffiClient/HttpClient), and
jobsniffer.http.curl_client needs a logger of its own -- if curl_client
imported create_logger from jobsniffer.util directly, that would be a
circular import (util -> http -> curl_client -> util). This module has no
dependency on either, so both sides can import it safely.

Re-exported from jobsniffer.util for the 8 existing scrapers that already
do `from jobsniffer.util import create_logger`.
"""

from __future__ import annotations

import logging


def create_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"JobSpy:{name}")
    logger.propagate = False
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        format = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
        formatter = logging.Formatter(format)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    return logger


def set_logger_level(verbose: int) -> None:
    """Adjusts the logger's level. This function allows the logging level to be changed at runtime.

    Parameters:
    - verbose: int {0, 1, 2} (default=2, all logs)
    """
    if verbose is None:
        return
    # .get()'s default of "INFO" means level_name is always one of
    # "INFO"/"WARNING"/"ERROR", so getattr(logging, level_name) always
    # resolves -- no None-check/ValueError branch needed (that branch was
    # dead code carried over from upstream JobSpy; removed rather than
    # kept behind a pragma).
    level_name = {2: "INFO", 1: "WARNING", 0: "ERROR"}.get(verbose, "INFO")
    level = getattr(logging, level_name)
    for logger_name in logging.root.manager.loggerDict:
        if logger_name.startswith("JobSpy:"):
            logging.getLogger(logger_name).setLevel(level)
