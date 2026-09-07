from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any

SENSITIVE_KEYS = {
    "prompt",
    "raw_prompt",
    "messages",
    "input",
    "authorization",
    "api_key",
    "token",
    "password",
    "secret",
    "generated_text",
    "model_output",
}

_REQUEST_EVENT_LOG_MAX_BYTES = 10 * 1024 * 1024
_REQUEST_EVENT_LOG_BACKUP_COUNT = 5


class _RequestEventFileHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging API
        # 표준 Handler는 emit 오류를 stderr에만 쓰고 삼킨다. 호출자가 stdout fallback을
        # 수행할 수 있도록 이 sink에서는 예외를 다시 올린다.
        raise


_request_event_handlers: dict[str, _RequestEventFileHandler] = {}
_request_event_handlers_lock = Lock()


def scrub_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else scrub_for_log(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_for_log(item) for item in value]
    return value


def service_logger(service: str) -> logging.Logger:
    logger = logging.getLogger(f"ai_model_serving.{service}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def emit_request_event(*, service: str, message: str, fallback_logger: logging.Logger) -> None:
    """요청 이벤트를 앱 소유 JSONL에 기록하고, 미설정·실패 시 stdout으로 보낸다.

    ``REQUEST_EVENT_LOG_DIR``은 Compose가 공통 고정 경로로 주입하는 내부 배치
    설정이다. 파일명은 서비스 식별자에서 유도한다. 설정되지 않은 app-only 실행은
    기존 stdout 동작을 유지한다. 관측 경로 장애가 API 응답을 실패시키면 안 되므로
    파일 준비나 rollover가 실패해도 stdout fallback으로 요청 레코드를 보존한다.
    """
    configured_dir = os.environ.get("REQUEST_EVENT_LOG_DIR", "").strip()
    if not configured_dir:
        fallback_logger.info(message)
        return

    try:
        path = str((Path(configured_dir) / f"{service}.jsonl").resolve())
        with _request_event_handlers_lock:
            handler = _request_event_handlers.get(path)
            if handler is None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                handler = _RequestEventFileHandler(
                    path,
                    maxBytes=_REQUEST_EVENT_LOG_MAX_BYTES,
                    backupCount=_REQUEST_EVENT_LOG_BACKUP_COUNT,
                    encoding="utf-8",
                    delay=True,
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                _request_event_handlers[path] = handler
        record = logging.LogRecord(
            name=f"ai_model_serving.request_events.{service}",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        handler.handle(record)
    except Exception:
        fallback_logger.exception("request event file sink failed; falling back to stdout")
        fallback_logger.info(message)
