from __future__ import annotations

import logging
import stat
from pathlib import Path

from server.observability.logging import configure_logging


def test_logging_keeps_stdout_concise_and_file_complete(
    tmp_path: Path, capsys
) -> None:
    root = logging.getLogger()
    original_level = root.level
    original_handlers = tuple(root.handlers)
    uvicorn_state = {
        name: (
            tuple(logging.getLogger(name).handlers),
            logging.getLogger(name).propagate,
            logging.getLogger(name).level,
        )
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    }
    log_file = tmp_path / "safactory-server.log"

    try:
        configure_logging("INFO", log_file, file_level="DEBUG")
        logging.getLogger("server.orchestrator").info("event=job_succeeded")
        logging.getLogger("server.orchestrator.detail").debug(
            "event=rjob_reconciliation_started"
        )
        logging.getLogger("brainpp.rjob.client").info("sdk_poll_noise")
        logging.getLogger("brainpp.rjob.client").warning("sdk_warning")

        for handler in root.handlers:
            handler.flush()

        stdout = capsys.readouterr().out
        file_content = log_file.read_text(encoding="utf-8")

        assert "event=job_succeeded" in stdout
        assert "sdk_warning" in stdout
        assert "rjob_reconciliation_started" not in stdout
        assert "sdk_poll_noise" not in stdout

        assert "event=job_succeeded" in file_content
        assert "rjob_reconciliation_started" in file_content
        assert "sdk_poll_noise" in file_content
        assert "sdk_warning" in file_content
        assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    finally:
        for handler in tuple(root.handlers):
            if handler not in original_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(original_level)
        for name, (handlers, propagate, level) in uvicorn_state.items():
            logger = logging.getLogger(name)
            logger.handlers[:] = handlers
            logger.propagate = propagate
            logger.setLevel(level)
