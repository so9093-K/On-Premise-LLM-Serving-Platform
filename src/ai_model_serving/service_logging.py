from __future__ import annotations

import logging
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
