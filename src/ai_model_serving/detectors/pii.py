from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..risk import assessment_response

DETECTOR_SOURCE = "pii-protection"

# 엔티티 라벨 -> D-code 매핑
_ENTITY_CODE: dict[str, str] = {
    "KR_RRN": "D1",
    "KR_FRN": "D1",
    "KR_PASSPORT": "D1",
    "KR_DRIVER_LICENSE": "D1",
    "EMAIL_ADDRESS": "D2",
    "PHONE_NUMBER": "D2",
    "IP_ADDRESS": "D5",
}


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


# --- 한국어 커스텀 인식기 패턴 (강한 패턴이라 컨텍스트 불필요) ---

_KR_RRN_RE = re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")
_KR_FRN_RE = re.compile(r"(?<!\d)\d{6}-[5-8]\d{6}(?!\d)")
_KR_PASSPORT_RE = re.compile(r"(?<![A-Z0-9])[MR][A-Z]\d{7}(?![A-Z0-9])")
_KR_DRIVER_LICENSE_RE = re.compile(r"(?<!\d)\d{2}-\d{2}-\d{6}-\d{2}(?!\d)")
# RFC 5321 local-part + domain — ASCII lookaround으로 한글/ASCII가 섞인 경계를 처리한다.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_KR_PHONE_RE = re.compile(
    r"(?<!\d)(?:"
    r"01[016789]-\d{3,4}-\d{4}"   # 휴대폰 (010/011/016/017/018/019)
    r"|02-\d{3,4}-\d{4}"          # 서울 유선
    r"|0[3-9]\d-\d{3,4}-\d{4}"    # 지역 유선 (031~099)
    r")(?!\d)"
)
_IP_ADDRESS_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")

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
        ("KR_PASSPORT", _KR_PASSPORT_RE),
        ("KR_DRIVER_LICENSE", _KR_DRIVER_LICENSE_RE),
        ("IP_ADDRESS", _IP_ADDRESS_RE),
        ("EMAIL_ADDRESS", _EMAIL_RE),
        ("PHONE_NUMBER", _KR_PHONE_RE),
    ):
        spans.extend(_regex_spans(text, entity, pattern))

    return spans


def _same_span(left: EntitySpan, right: EntitySpan) -> bool:
    return left.start == right.start and left.end == right.end


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

    # 정확히 동일한 엔티티이면서 정확히 동일한 range일 때만 duplicate로 본다. 부분
    # overlap은 reconciler가 의미를 추론하지 않으므로 그대로 보존한다.
    deduplicated: list[EntitySpan] = []
    for candidate in candidates:
        if any(
            accepted.entity == candidate.entity and _same_span(accepted, candidate)
            for accepted in deduplicated
        ):
            continue
        deduplicated.append(candidate)

    return deduplicated


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
    """Collect deterministic local findings without external model or cache I/O."""
    return _run_custom_span_recognizers(text)


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


def mask_pii(text: str) -> str:
    """탐지된 PII span을 라벨로 치환한 텍스트를 반환한다.

    assess()와 달리 원문 offset을 실제로 텍스트에 적용한다 -- 디버그 로깅처럼
    사람이 읽을 원문이 필요한 소비처 전용이며, risk 판정 경로(assess)와는 분리된다.
    """
    spans = _reconcile_spans(_collect_recognizer_spans(text))
    for span in sorted(spans, key=lambda item: item.start, reverse=True):
        text = text[: span.start] + f"[{span.entity}]" + text[span.end :]
    return text


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
