from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import phonenumbers

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
    """중복 제거 전 recognizer가 발견한 엔티티 하나를 표현한다.

    Raw matched text is intentionally not stored. Character offsets are retained
    only in-process so overlapping matches can be deduplicated without exposing
    PII in the API response.
    """

    entity: str
    start: int
    end: int
    # 체크섬·지역코드 같은 자체 검증을 통과했으면 1.0, 패턴만 맞으면 0.5,
    # 검증 수단이 없는 엔티티(이메일·IP·여권)는 None이다.
    confidence: float | None = None


@dataclass(frozen=True)
class EntitySummary:
    """recognizer 결과를 정리한 뒤 생성한 분류별 탐지 개수다."""

    entity: str
    code: str
    span_count: int
    confidence: float | None = None


# --- 한국어 커스텀 인식기 패턴 (강한 패턴이라 컨텍스트 불필요) ---

# 아래 한국 식별번호 패턴과 검증 로직은 Microsoft Presidio(MIT)의
# presidio_analyzer/predefined_recognizers/country_specific/korea 를 옮긴 것이다.
# 패키지 자체를 의존성에 넣지 않은 이유는 recognizer 하나만 import해도 spacy·
# thinc·numpy·blis가 함께 로드돼(모듈 1,300개 이상, 설치 373MB) 이 탐지기의
# "외부 모델 없이 결정적으로 동작한다"는 성질이 깨지기 때문이다.

# 생년월일(월 01-12, 일 01-31)까지 검증하고 하이픈은 선택이다. 예전에는
# `\d{6}-` 였어서 `9012011234567`처럼 하이픈 없는 표기를 통째로 놓쳤다.
_KR_RRN_RE = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-?[1-4]\d{6}(?!\d)")
_KR_FRN_RE = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-?[5-8]\d{6}(?!\d)")
# 여권: 구형 `M12345678`(영문1+숫자8), 신형 `M123A4567`(영문1+숫자3+영문1+숫자4).
# 예전 패턴 `[MR][A-Z]\d{7}`은 둘 중 어느 쪽도 아니어서 실제 여권번호를 하나도
# 잡지 못했다. 접두 문자도 M/R뿐이었으나 실제로는 M|S|R|O|D를 쓴다.
# 신형(영문1+숫자3+영문1+숫자4)은 형태가 특이해 단독으로 써도 오탐이 거의 없다.
_KR_PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[MSRODmsrod]\d{3}[A-Za-z]\d{4}(?![0-9])")
# 구형(영문1+숫자8)은 제품코드·주문번호·리비전과 형태가 겹친다(`S24010203`,
# `D20260901`, `R12345678`). 그래서 근처에 여권을 가리키는 단어가 있을 때만
# 인정한다 -- Presidio도 같은 이유로 이 패턴에 0.05를 주고 context에 의존한다.
_KR_PASSPORT_LEGACY_RE = re.compile(r"(?<![A-Za-z0-9])[MSRODmsrod]\d{8}(?![0-9])")
_PASSPORT_CONTEXT_RE = re.compile(r"여권|passport", re.IGNORECASE)
_PASSPORT_CONTEXT_WINDOW = 30
# 구분자는 하이픈·공백·없음 모두 허용한다. 지역번호는 아래에서 따로 검증한다.
_KR_DRIVER_LICENSE_RE = re.compile(r"(?<!\d)\d{2}[- ]?\d{2}[- ]?\d{6}[- ]?\d{2}(?!\d)")
_KR_DRIVER_LICENSE_REGIONS = frozenset(
    {"11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "28"}
)
# RFC 5321 local-part + domain — ASCII lookaround으로 한글/ASCII가 섞인 경계를 처리한다.
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# 전화번호는 정규식을 직접 쓰지 않고 Google libphonenumber의 파이썬 포팅에 맡긴다.
# 직접 쓴 패턴은 하이픈이 있는 표기만 잡아서 `01012345678`, `010 1234 5678`,
# `+82-10-1234-5678` 같은 흔한 형태를 놓쳤다. PhoneNumberMatcher는 원문 offset을
# 그대로 돌려주므로 EntitySpan에 바로 연결되고, 의존성이 없는 순수 파이썬이라
# "외부 모델 없이 결정적으로 동작한다"도 유지된다.
_PHONE_REGION = "KR"
# 옥텟을 0-255로 제한한다. 예전에는 `\d{1,3}`이라 `999.999.999.999`도 IP로 잡혔다.
_IP_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
_IP_ADDRESS_RE = re.compile(rf"(?<![\d.])(?:{_IP_OCTET}\.){{3}}{_IP_OCTET}(?![\d.])")
# `scheme://` 뒤의 authority 구간. RFC 3986의 authority는 첫 `/`, `?`, `#`,
# 공백에서 끝난다. 이 구간 안의 `userinfo@host`는 이메일 주소가 아니라 URI
# 자격증명이므로 EMAIL_ADDRESS로 잡으면 안 된다 -- 예전에는
# `postgresql://user:password@db.example.com`이 EMAIL_ADDRESS(D2)로 분류돼,
# secret detector가 같은 문자열에 붙인 DATABASE_URL(D5)을 strongest_code에서
# 가렸다. authority 밖(경로·쿼리·mailto:·평문)의 이메일은 그대로 탐지한다.
_URI_AUTHORITY_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/?#]*")

_RRN_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)


def _rrn_checksum_ok(digits: str) -> bool:
    """앞 12자리 가중합으로 마지막 검증번호를 재계산해 대조한다."""
    total = sum(int(digits[i]) * _RRN_WEIGHTS[i] for i in range(12))
    return (11 - (total % 11)) % 10 == int(digits[12])


def _kr_registration_confidence(matched: str) -> float:
    """주민/외국인등록번호의 신뢰도.

    2020년 10월부터 뒷자리는 "성별 표시 첫 자리를 제외하고 6자리의 임의번호"라
    검증번호 자리가 임의값으로 바뀌었다. 그래서 체크섬 불일치를 탈락 사유로
    쓰면 그 이후 발급분을 통째로 놓친다 -- signal-only 계약에서 미탐은 오탐보다
    나쁘므로, 체크섬은 거르는 조건이 아니라 신뢰도를 올리는 신호로만 쓴다.
    """
    digits = matched.replace("-", "")
    return 1.0 if len(digits) == 13 and _rrn_checksum_ok(digits) else 0.5


def _kr_driver_license_confidence(matched: str) -> float | None:
    """지역번호(앞 2자리)가 실제 발급 지역이 아니면 탐지에서 제외한다."""
    digits = re.sub(r"[- ]", "", matched)
    if len(digits) != 12 or digits[:2] not in _KR_DRIVER_LICENSE_REGIONS:
        return None
    return 1.0


# 엔티티별 추가 검증. None을 돌려주면 그 span은 버린다.
_SPAN_VALIDATORS = {
    "KR_RRN": _kr_registration_confidence,
    "KR_FRN": _kr_registration_confidence,
    "KR_DRIVER_LICENSE": _kr_driver_license_confidence,
}


def _regex_spans(
    text: str,
    entity: str,
    pattern: re.Pattern[str],
) -> list[EntitySpan]:
    validator = _SPAN_VALIDATORS.get(entity)
    spans: list[EntitySpan] = []
    for match in pattern.finditer(text):
        confidence = validator(match.group(0)) if validator is not None else None
        if validator is not None and confidence is None:
            continue
        spans.append(EntitySpan(entity, match.start(), match.end(), confidence))
    return spans


def _run_custom_span_recognizers(text: str) -> list[EntitySpan]:
    """신뢰도 높은 local recognizer 결과와 문자 offset을 반환한다.

    외부 모델이나 cache I/O 없이 결정적으로 동작한다.
    """
    spans: list[EntitySpan] = []
    for entity, pattern in (
        ("KR_RRN", _KR_RRN_RE),
        ("KR_FRN", _KR_FRN_RE),
        ("KR_PASSPORT", _KR_PASSPORT_RE),
        ("KR_PASSPORT_LEGACY", _KR_PASSPORT_LEGACY_RE),
        ("KR_DRIVER_LICENSE", _KR_DRIVER_LICENSE_RE),
        ("IP_ADDRESS", _IP_ADDRESS_RE),
        ("EMAIL_ADDRESS", _EMAIL_RE),
    ):
        spans.extend(_regex_spans(text, entity, pattern))
    spans.extend(_phone_spans(text))
    spans = _passport_legacy_spans(text, spans)

    return _drop_email_spans_inside_uri_authority(text, spans)


def _passport_legacy_spans(text: str, spans: list[EntitySpan]) -> list[EntitySpan]:
    """구형 여권 후보를 문맥이 있을 때만 KR_PASSPORT로 승격하고, 아니면 버린다."""
    kept: list[EntitySpan] = []
    for span in spans:
        if span.entity != "KR_PASSPORT_LEGACY":
            kept.append(span)
            continue
        window = text[max(0, span.start - _PASSPORT_CONTEXT_WINDOW) : span.end + _PASSPORT_CONTEXT_WINDOW]
        if _PASSPORT_CONTEXT_RE.search(window):
            kept.append(EntitySpan("KR_PASSPORT", span.start, span.end, span.confidence))
    return kept


def _phone_spans(text: str) -> list[EntitySpan]:
    """libphonenumber가 유효하다고 판정한 전화번호만 span으로 만든다."""
    spans: list[EntitySpan] = []
    for match in phonenumbers.PhoneNumberMatcher(text, _PHONE_REGION):
        if not phonenumbers.is_valid_number(match.number):
            continue
        spans.append(EntitySpan("PHONE_NUMBER", match.start, match.end, 1.0))
    return spans


def _drop_email_spans_inside_uri_authority(
    text: str,
    spans: list[EntitySpan],
) -> list[EntitySpan]:
    """URI authority 안에 통째로 들어있는 EMAIL_ADDRESS span을 버린다."""
    authorities = [
        (match.start(), match.end()) for match in _URI_AUTHORITY_RE.finditer(text)
    ]
    if not authorities:
        return spans
    return [
        span
        for span in spans
        if span.entity != "EMAIL_ADDRESS"
        or not any(start <= span.start and span.end <= end for start, end in authorities)
    ]


def _same_span(left: EntitySpan, right: EntitySpan) -> bool:
    return left.start == right.start and left.end == right.end


def _reconcile_spans(spans: list[EntitySpan]) -> list[EntitySpan]:
    """서로 독립적인 탐지는 보존하면서 기술적 중복만 제거한다."""
    candidates = sorted(
        spans,
        key=lambda span: (span.start, span.end, span.entity),
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


def _entity_confidence(spans: list[EntitySpan], entity: str) -> float | None:
    """같은 엔티티 span 중 가장 높은 신뢰도. 전부 None이면 None."""
    values = [s.confidence for s in spans if s.entity == entity and s.confidence is not None]
    return max(values) if values else None


def _classify_spans(spans: list[EntitySpan]) -> list[EntitySummary]:
    """정리된 엔티티 span을 공개 D-code 분류 체계에 매핑한다."""
    return [
        EntitySummary(
            entity=entity,
            code=_ENTITY_CODE[entity],
            span_count=count,
            confidence=_entity_confidence(spans, entity),
        )
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
    """인식·중복 정리 없이 이미 분류된 결과로 계약 category를 만든다."""
    if not summaries:
        return [_safe_category()]

    return [
        {
            "code": summary.code,
            "family": "data_exposure",
            "detected": True,
            "confidence": summary.confidence,
            "source_model": DETECTOR_SOURCE,
            "label": summary.entity,
            "span_count": summary.span_count,
        }
        for summary in summaries
    ]


def _build_assessment(categories: list[dict[str, Any]]) -> dict[str, Any]:
    """이미 분류된 category로 외부 assessment 계약 응답을 만든다."""
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
    spans = _reconcile_spans(_run_custom_span_recognizers(text))
    for span in sorted(spans, key=lambda item: item.start, reverse=True):
        text = text[: span.start] + f"[{span.entity}]" + text[span.end :]
    return text


class PIIProtectionDetector:
    """인식, 기술적 중복 제거, 분류, 응답 생성을 순서대로 조율한다.

    Each stage is implemented separately. The detector does not validate whether
    an identifier exists or belongs to a real person or business.
    """

    async def assess(self, text: str) -> dict[str, Any]:
        recognized = _run_custom_span_recognizers(text)
        reconciled = _reconcile_spans(recognized)
        summaries = _classify_spans(reconciled)
        categories = _categories_from_summaries(summaries)
        return _build_assessment(categories)
