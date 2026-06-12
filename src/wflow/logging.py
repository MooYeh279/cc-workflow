"""File-based logging with date-partitioned directories.

Directory structure:
    logs/
    ├── 2026-06-10/
    │   ├── server.log              # App-level: startup, API requests, errors
    │   ├── run-<uuid>.log           # Per-workflow-run execution log
    │   └── ...
    └── 2026-06-11/
        └── ...

Usage:
    from wflow.logging import get_server_logger, get_run_logger

    # Server-level logging
    logger = get_server_logger()
    logger.info("Server started on port 8100")

    # Per-run logging
    run_logger = get_run_logger("run-uuid-here")
    run_logger.info("Executing node: coding")
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOG_DIR: Optional[Path] = None
_RUN_LOGGERS: dict[str, logging.Logger] = {}
_SERVER_LOGGER: Optional[logging.Logger] = None

DEFAULT_LOG_DIR = Path("logs")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _timestamp_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


LOG_FORMAT = logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)-24s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

LOG_FORMAT_DETAIL = logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)-24s | %(filename)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def setup_logging(log_dir: str | Path = DEFAULT_LOG_DIR) -> None:
    """Initialize the logging system. Call once at application startup.

    Creates the log directory for today and sets up the server logger.
    Idempotent — subsequent calls are no-ops.
    """
    global _LOG_DIR, _SERVER_LOGGER
    if _SERVER_LOGGER is not None:
        return  # Already initialized
    _LOG_DIR = Path(log_dir)

    # Create today's log directory
    today_dir = _LOG_DIR / _today_str()
    today_dir.mkdir(parents=True, exist_ok=True)

    # Server logger
    _SERVER_LOGGER = logging.getLogger("wflow.server")
    _SERVER_LOGGER.setLevel(logging.DEBUG)

    # Console handler (INFO+)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(LOG_FORMAT)
    _SERVER_LOGGER.addHandler(console)

    # File handler (DEBUG+) — writes to today's server.log
    server_log_path = today_dir / "server.log"
    file_handler = logging.FileHandler(str(server_log_path), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(LOG_FORMAT_DETAIL)
    _SERVER_LOGGER.addHandler(file_handler)

    _SERVER_LOGGER.info(f"Logging initialized — directory: {_LOG_DIR.resolve()}")


def get_server_logger() -> logging.Logger:
    """Return the server-level logger. Creates a default if setup_logging was not called."""
    global _SERVER_LOGGER
    if _SERVER_LOGGER is None:
        setup_logging()
    return _SERVER_LOGGER  # type: ignore[return-value]


def get_run_logger(run_id: str) -> logging.Logger:
    """Return a logger for a specific workflow run.

    Logs go to: logs/YYYY-MM-DD/run-<short-uuid>.log
    A short prefix of the run_id is used in the filename for readability.
    """
    global _LOG_DIR, _RUN_LOGGERS

    if run_id in _RUN_LOGGERS:
        return _RUN_LOGGERS[run_id]

    if _LOG_DIR is None:
        _LOG_DIR = DEFAULT_LOG_DIR

    today_dir = _LOG_DIR / _today_str()
    today_dir.mkdir(parents=True, exist_ok=True)

    short_id = run_id[:8]
    log_path = today_dir / f"run-{short_id}.log"

    logger_name = f"wflow.run.{short_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # Don't bubble to root logger

    # File handler
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(LOG_FORMAT_DETAIL)
    logger.addHandler(fh)

    # Also log to console at INFO level
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(LOG_FORMAT)
    logger.addHandler(ch)

    logger.info(f"=== Run {run_id} started ===")

    _RUN_LOGGERS[run_id] = logger
    return logger


def close_run_logger(run_id: str) -> None:
    """Close and remove a run logger (called when run completes)."""
    global _RUN_LOGGERS
    logger = _RUN_LOGGERS.pop(run_id, None)
    if logger is not None:
        logger.info(f"=== Run {run_id} finished ===")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


def get_log_path(run_id: str) -> Optional[Path]:
    """Return the log file path for a given run, if it exists."""
    global _LOG_DIR
    if _LOG_DIR is None:
        return None
    path = _LOG_DIR / _today_str() / f"run-{run_id[:8]}.log"
    return path if path.exists() else None
