from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..risk import assessment_response

DETECTOR_SOURCE = "pii-protection"

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

# Presidio built-in entities to request.
# PERSON and ADDRESS are NLP/NER-based; en_core_web_sm on Korean text produces
# false positives for PERSON and no recognizer exists for ADDRESS in English.
_PRESIDIO_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "URL",
]


@dataclass(frozen=True)
class EntitySpan:
    """One recognizer finding before duplicate removal.

    Raw matched text is intentionally not stored. Character offsets are retained
    only in-process so multiple recognizers can be deduplicated without exposing
    PII in the API response.
    """

    entity: str
    start: int
    end: int
    source: str


@dataclass(frozen=True)
class EntitySummary:
    """Classified count produced after recognizer reconciliation."""

    entity: str
    code: str
    span_count: int


# --- Korean custom recognizer patterns (no context required for strong patterns) ---

_KR_RRN_RE = re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")
_KR_FRN_RE = re.compile(r"(?<!\d)\d{6}-[5-8]\d{6}(?!\d)")
_KR_BRN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)")
_KR_PASSPORT_RE = re.compile(r"(?<![A-Z0-9])[MR][A-Z]\d{7}(?![A-Z0-9])")
_KR_DRIVER_LICENSE_RE = re.compile(r"(?<!\d)\d{2}-\d{2}-\d{6}-\d{2}(?!\d)")
# RFC 5321 local-part + domain — ASCII lookarounds handle mixed Korean/ASCII boundaries.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_KR_PHONE_RE = re.compile(
    r"(?<!\d)(?:"
    r"01[016789]-\d{3,4}-\d{4}"   # 휴대폰 (010/011/016/017/018/019)
    r"|02-\d{3,4}-\d{4}"          # 서울 유선
    r"|0[3-9]\d-\d{3,4}-\d{4}"    # 지역 유선 (031~099)
    r")(?!\d)"
)
_BANK_ACCOUNT_RE = re.compile(r"(?<!\d)\d{10,14}(?!\d)")
_BANK_ACCOUNT_CONTEXT_RE = re.compile(
    r"(?i)(계좌|입금|출금|이체|account|bank|통장)",
)
_BANK_ACCOUNT_CONTEXT_WINDOW = 16

_PHONE_SHAPED_IDENTIFIER_ENTITIES: frozenset[str] = frozenset(
    {"KR_RRN", "KR_FRN", "KR_BRN", "KR_DRIVER_LICENSE"}
)


def _regex_spans(
    text: str,
    entity: str,
    pattern: re.Pattern[str],
) -> list[EntitySpan]:
    return [
        EntitySpan(entity, match.start(), match.end(), "custom")
        for match in pattern.finditer(text)
    ]


def _run_custom_span_recognizers(text: str) -> list[EntitySpan]:
    """Return strong local recognizer findings with their character offsets."""
    spans: list[EntitySpan] = []
    for entity, pattern in (
        ("KR_RRN", _KR_RRN_RE),
        ("KR_FRN", _KR_FRN_RE),
        ("KR_BRN", _KR_BRN_RE),
        ("KR_PASSPORT", _KR_PASSPORT_RE),
        ("KR_DRIVER_LICENSE", _KR_DRIVER_LICENSE_RE),
        ("EMAIL_ADDRESS", _EMAIL_RE),
        ("PHONE_NUMBER", _KR_PHONE_RE),
    ):
        spans.extend(_regex_spans(text, entity, pattern))

    for match in _BANK_ACCOUNT_RE.finditer(text):
        context_start = max(0, match.start() - _BANK_ACCOUNT_CONTEXT_WINDOW)
        context_end = min(len(text), match.end() + _BANK_ACCOUNT_CONTEXT_WINDOW)
        if _BANK_ACCOUNT_CONTEXT_RE.search(text[context_start:context_end]):
            spans.append(
                EntitySpan(
                    "BANK_ACCOUNT_CANDIDATE",
                    match.start(),
                    match.end(),
                    "custom",
                )
            )
    return spans


def _try_presidio_span_analysis(text: str) -> list[EntitySpan] | None:
    """Run Presidio while retaining offsets needed for reconciliation."""
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import-untyped]
    except ImportError:
        return None

    engine = _get_analyzer()
    results = engine.analyze(text=text, language="en", entities=_PRESIDIO_ENTITIES)
    return [
        EntitySpan(
            entity=result.entity_type,
            start=int(result.start),
            end=int(result.end),
            source="presidio",
        )
        for result in results
    ]


def _same_span(left: EntitySpan, right: EntitySpan) -> bool:
    return left.start == right.start and left.end == right.end


def _contains(container: EntitySpan, nested: EntitySpan) -> bool:
    return container.start <= nested.start and nested.end <= container.end


def _is_nested_duplicate(preferred: EntitySpan, candidate: EntitySpan) -> bool:
    """Return true only for known recognizer duplication, not general overlap."""
    if (
        preferred.entity in _PHONE_SHAPED_IDENTIFIER_ENTITIES
        and candidate.entity == "PHONE_NUMBER"
    ):
        return _same_span(preferred, candidate)
    if preferred.entity == "EMAIL_ADDRESS" and candidate.entity == "URL":
        # Two suppression cases for URLs that co-locate with an email:
        # 1) URL is inside email (e.g. domain "example.com" ⊆ "hong@example.com"):
        #    candidate.end <= preferred.end
        # 2) URL wraps email due to SpaCy extending past Korean boundary
        #    (e.g. "hong@example.com이고" → URL=[9,28) vs EMAIL=[9,25)):
        #    candidate.start == preferred.start
        # A URL that starts *inside* the email span and extends *beyond* it is a
        # distinct entity (different semantics) and must be kept.
        return candidate.start == preferred.start or candidate.end <= preferred.end
    return False


def _reconcile_spans(spans: list[EntitySpan]) -> list[EntitySpan]:
    """Remove technical duplicates while preserving independent findings."""
    candidates = sorted(
        spans,
        key=lambda span: (
            span.start,
            span.end,
            span.entity,
            0 if span.source == "custom" else 1,
        ),
    )

    # Only the exact same entity and exact same range is a duplicate. Partial
    # overlap is preserved because the reconciler does not infer meaning.
    deduplicated: list[EntitySpan] = []
    for candidate in candidates:
        if any(
            accepted.entity == candidate.entity and _same_span(accepted, candidate)
            for accepted in deduplicated
        ):
            continue
        deduplicated.append(candidate)

    return [
        candidate
        for candidate in deduplicated
        if not any(
            other is not candidate and _is_nested_duplicate(other, candidate)
            for other in deduplicated
        )
    ]


def _count_spans(spans: list[EntitySpan]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in spans:
        counts[span.entity] = counts.get(span.entity, 0) + 1
    return counts


def _classify_spans(spans: list[EntitySpan]) -> list[EntitySummary]:
    """Map reconciled entity spans to the public D-code taxonomy."""
    return [
        EntitySummary(entity=entity, code=_ENTITY_CODE[entity], span_count=count)
        for entity, count in sorted(_count_spans(spans).items())
        if entity in _ENTITY_CODE
    ]


_analyzer_instance: Any = None

_SPACY_MODEL = "en_core_web_sm"


def _get_analyzer() -> Any:
    global _analyzer_instance
    if _analyzer_instance is None:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import-untyped]
        from presidio_analyzer.nlp_engine import NlpEngineProvider  # type: ignore[import-untyped]
        nlp_engine = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": _SPACY_MODEL}],
        }).create_engine()
        _analyzer_instance = AnalyzerEngine(nlp_engine=nlp_engine)
    return _analyzer_instance


def _safe_category() -> dict[str, Any]:
    return {
        "code": None,
        "family": "data_exposure",
        "detected": False,
        "confidence": None,
        "source_model": DETECTOR_SOURCE,
        "label": None,
        "span_count": 0,
    }


def _categories_from_summaries(
    summaries: list[EntitySummary],
) -> list[dict[str, Any]]:
    """Build contract categories without performing recognition or reconciliation."""
    if not summaries:
        return [_safe_category()]

    return [
        {
            "code": summary.code,
            "family": "data_exposure",
            "detected": True,
            "confidence": None,
            "source_model": DETECTOR_SOURCE,
            "label": summary.entity,
            "span_count": summary.span_count,
        }
        for summary in summaries
    ]


def _collect_recognizer_spans(text: str) -> list[EntitySpan]:
    """Collect findings; each recognizer remains responsible only for detection."""
    spans = _run_custom_span_recognizers(text)
    presidio_spans = _try_presidio_span_analysis(text)
    if presidio_spans is not None:
        spans.extend(presidio_spans)
    return spans


def _build_assessment(categories: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the external assessment contract from already classified categories."""
    detected = any(category["detected"] for category in categories)
    message = "Data exposure signal detected." if detected else "No PII signal detected."
    return assessment_response(
        categories=categories,
        system_signals=[],
        status="completed",
        message=message,
    )


class PIIProtectionDetector:
    """Coordinate recognition, technical deduplication, classification, and output.

    Each stage is implemented separately. The detector does not validate whether
    an identifier exists or belongs to a real person or business.
    """

    async def assess(self, text: str) -> dict[str, Any]:
        recognized = _collect_recognizer_spans(text)
        reconciled = _reconcile_spans(recognized)
        summaries = _classify_spans(reconciled)
        categories = _categories_from_summaries(summaries)
        return _build_assessment(categories)
