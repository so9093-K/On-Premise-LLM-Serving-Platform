from __future__ import annotations

"""Compatibility facade for runtime contract validators.

New code should import validators from ``ai_model_serving.contracts`` modules.
This facade keeps the historic ``ai_model_serving.validation`` API stable while
chat, embedding, media, and risk contracts evolve independently.
"""

from .contracts.chat import (
    CHAT_ROLES,
    TOOL_CHAT_ROLES,
    UNSUPPORTED_CHAT_FIELDS,
    UNSUPPORTED_MESSAGE_FIELDS,
    validate_chat_request,
    validate_chat_response,
)
from .contracts.common import ensure_object
from .contracts.embedding import EMBEDDING_DIMENSIONS, expected_embedding_count, requested_embedding_dimensions, validate_embedding_request, validate_embedding_response
from .contracts.risk import (
    FORBIDDEN_RISK_RESPONSE_FIELDS,
    MAX_RISK_PROMPT_LENGTH,
    MODEL_RISK_CODES,
    RISK_RESPONSE_REQUIRED_FIELDS,
    RISK_RESPONSE_STATUS,
    SYSTEM_RISK_CODES,
    read_risk_prompt,
    validate_risk_response,
)

__all__ = [
    "CHAT_ROLES",
    "EMBEDDING_DIMENSIONS",
    "FORBIDDEN_RISK_RESPONSE_FIELDS",
    "MAX_RISK_PROMPT_LENGTH",
    "MODEL_RISK_CODES",
    "RISK_RESPONSE_REQUIRED_FIELDS",
    "RISK_RESPONSE_STATUS",
    "SYSTEM_RISK_CODES",
    "TOOL_CHAT_ROLES",
    "UNSUPPORTED_CHAT_FIELDS",
    "UNSUPPORTED_MESSAGE_FIELDS",
    "ensure_object",
    "read_risk_prompt",
    "validate_chat_request",
    "validate_chat_response",
    "expected_embedding_count",
    "requested_embedding_dimensions",
    "validate_embedding_request",
    "validate_embedding_response",
    "validate_risk_response",
]
