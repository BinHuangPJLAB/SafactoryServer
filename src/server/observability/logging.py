from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_MANAGED_HANDLER_ATTRIBUTE = "_safactory_managed_handler"
_FILE_ONLY_LOGGER_PART = ".detail"
_STDOUT_LOGGER_PREFIXES = ("server.", "uvicorn.error")


class _KeyStdoutFilter(logging.Filter):
    """Keep stdout concise while leaving the rotating file unfiltered."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        if _FILE_ONLY_LOGGER_PART in record.name:
            return False
        return record.name.startswith(_STDOUT_LOGGER_PREFIXES)


class _SecureRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream


def configure_logging(
    stdout_level: str,
    log_file: Path | None = None,
    *,
    file_level: str = "DEBUG",
    max_bytes: int = 100 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Configure concise stdout and a complete rotating diagnostic log."""

    root = logging.getLogger()
    _remove_previous_runtime_handlers(root)

    stdout_handler = logging.StreamHandler(sys.stdout)
    setattr(stdout_handler, _MANAGED_HANDLER_ATTRIBUTE, True)
    stdout_handler.setLevel(_level_number(stdout_level))
    stdout_handler.addFilter(_KeyStdoutFilter())
    stdout_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(stdout_handler)

    handler_levels = [stdout_handler.level]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = _SecureRotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        setattr(file_handler, _MANAGED_HANDLER_ATTRIBUTE, True)
        file_handler.setLevel(_level_number(file_level))
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s "
                "pid=%(process)d %(filename)s:%(lineno)d %(message)s"
            )
        )
        root.addHandler(file_handler)
        handler_levels.append(file_handler.level)

    root.setLevel(min(handler_levels))
    _route_uvicorn_through_root()


def _remove_previous_runtime_handlers(root: logging.Logger) -> None:
    for handler in tuple(root.handlers):
        if getattr(handler, _MANAGED_HANDLER_ATTRIBUTE, False):
            root.removeHandler(handler)
            handler.close()


def _route_uvicorn_through_root() -> None:
    # Uvicorn installs dedicated stdout handlers. Routing through the root avoids
    # duplicate access lines and also puts its complete output in the file log.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def _level_number(value: str) -> int:
    level = logging.getLevelName(value.upper())
    if not isinstance(level, int):
        raise ValueError(f"invalid log level: {value}")
    return level
