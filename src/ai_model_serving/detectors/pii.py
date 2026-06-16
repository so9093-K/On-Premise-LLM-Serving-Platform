from __future__ import annotations

import re
from typing import Any

from ..risk import assessment_response

SOURCE_MODEL = "presidio-analyzer"

# Entity label -> D-code mapping
_ENTITY_CODE: dict[str, str] = {
    "KR_RRN": "D1",
    "KR_FRN": "D1",
    "KR_PASSPORT": "D1",
    "KR_DRIVER_LICENSE": "D1",
    "PERSON": "D1",
    "EMAIL_ADDRESS": "D2",
    "PHONE_NUMBER": "D2",
    "ADDRESS": "D2",
    "CREDIT_CARD": "D3",
    "KR_BRN": "D3",
    "BANK_ACCOUNT_CANDIDATE": "D3",
    "IP_ADDRESS": "D5",
    "URL": "D5",
}

# Presidio built-in entities to request
_PRESIDIO_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "PERSON",
    "ADDRESS",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "URL",
]

# --- Korean custom recognizer patterns (no context required for strong patterns) ---

_KR_RRN_RE = re.compile(r"\b(\d{6})-([1-4]\d{6})\b")
_KR_FRN_RE = re.compile(r"\b(\d{6})-([5-8]\d{6})\b")
_KR_BRN_RE = re.compile(r"\b(\d{3})-(\d{2})-(\d{5})\b")
_KR_PASSPORT_RE = re.compile(r"\b[MR][A-Z]\d{7}\b")
_KR_DRIVER_LICENSE_RE = re.compile(r"\b\d{2}-\d{2}-\d{6}-\d{2}\b")
_BANK_ACCOUNT_RE = re.compile(r"\b\d{10,14}\b")
_BANK_ACCOUNT_CONTEXT_RE = re.compile(
    r"(?i)(계좌|입금|출금|이체|account|bank|통장)",
)


def _run_custom_recognizers(text: str) -> dict[str, int]:
    """Run Korean custom recognizers and return entity_label -> span_count dict."""
    counts: dict[str, int] = {}

    if m := _KR_RRN_RE.findall(text):
        counts["KR_RRN"] = len(m)
    if m := _KR_FRN_RE.findall(text):
        counts["KR_FRN"] = len(m)
    if m := _KR_BRN_RE.findall(text):
        counts["KR_BRN"] = len(m)
    if m := _KR_PASSPORT_RE.findall(text):
        counts["KR_PASSPORT"] = len(m)
    if m := _KR_DRIVER_LICENSE_RE.findall(text):
        counts["KR_DRIVER_LICENSE"] = len(m)

    # Bank account candidate: only count when financial context keyword nearby
    if _BANK_ACCOUNT_CONTEXT_RE.search(text):
        if m := _BANK_ACCOUNT_RE.findall(text):
            counts["BANK_ACCOUNT_CANDIDATE"] = len(m)

    return counts


def _try_presidio_analysis(text: str) -> dict[str, int] | None:
    """Run Presidio analysis; return None if presidio is not installed."""
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import-untyped]
    except ImportError:
        return None

    engine = _get_analyzer()
    results = engine.analyze(text=text, language="en", entities=_PRESIDIO_ENTITIES)
    counts: dict[str, int] = {}
    for result in results:
        entity = result.entity_type
        counts[entity] = counts.get(entity, 0) + 1
    return counts


_analyzer_instance: Any = None


def _get_analyzer() -> Any:
    global _analyzer_instance
    if _analyzer_instance is None:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import-untyped]
        _analyzer_instance = AnalyzerEngine()
    return _analyzer_instance


def _build_categories(entity_counts: dict[str, int]) -> list[dict[str, Any]]:
    """Convert entity_label -> count mapping to category dicts."""
    if not entity_counts:
        return [
            {
                "code": None,
                "family": "data_exposure",
                "detected": False,
                "confidence": None,
                "source_model": SOURCE_MODEL,
                "label": None,
                "span_count": 0,
            }
        ]

    categories: list[dict[str, Any]] = []
    for entity_label, span_count in sorted(entity_counts.items()):
        code = _ENTITY_CODE.get(entity_label)
        if code is None:
            continue
        categories.append(
            {
                "code": code,
                "family": "data_exposure",
                "detected": True,
                "confidence": None,
                "source_model": SOURCE_MODEL,
                "label": entity_label,
                "span_count": span_count,
            }
        )

    if not categories:
        return [
            {
                "code": None,
                "family": "data_exposure",
                "detected": False,
                "confidence": None,
                "source_model": SOURCE_MODEL,
                "label": None,
                "span_count": 0,
            }
        ]
    return categories


class PIIProtectionDetector:
    """PII detection using Presidio Analyzer (optional) + Korean custom recognizers.

    Presidio is an optional runtime dependency. When not installed, only the
    Korean regex recognizers run. Original PII values are never included in the
    response; only entity labels and span counts are returned.
    """

    async def assess(self, text: str) -> dict[str, Any]:
        entity_counts: dict[str, int] = {}

        # Korean custom recognizers (always available)
        entity_counts.update(_run_custom_recognizers(text))

        # Presidio built-in recognizers (optional)
        presidio_counts = _try_presidio_analysis(text)
        if presidio_counts is not None:
            for entity, count in presidio_counts.items():
                entity_counts[entity] = entity_counts.get(entity, 0) + count

        categories = _build_categories(entity_counts)
        detected = any(c["detected"] for c in categories)
        message = "Data exposure signal detected." if detected else "No PII signal detected."
        return assessment_response(
            categories=categories,
            system_signals=[],
            status="completed",
            message=message,
        )
