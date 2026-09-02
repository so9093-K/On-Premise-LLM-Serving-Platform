from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

import phonenumbers

from ..risk import assessment_response
from .normalization import normalize_with_origin, to_origin_span
from .protocol import DetectionProfile

DETECTOR_SOURCE = "pii-protection"

# 엔티티 라벨 -> D-code 매핑
# CREDIT_CARD는 D1(Personal Identifier)에 넣는다. 계약이 허용하는 코드는
# A1/A2, I1-I4, D1/D2/D4/D5뿐이고(contracts/risk.py의 MODEL_RISK_CODES) 금융
# 식별자용 코드가 따로 없어, 새 코드를 만들지 않고 개인 식별자로 분류한다.
_ENTITY_CODE: dict[str, str] = {
    "KR_RRN": "D1",
    "KR_FRN": "D1",
    "KR_PASSPORT": "D1",
    "KR_DRIVER_LICENSE": "D1",
    "CREDIT_CARD": "D1",
    "EMAIL_ADDRESS": "D2",
    "PHONE_NUMBER": "D2",
    "IP_ADDRESS": "D5",
}

# 공개 문서(endpoint_spec)가 라벨 목록을 손으로 베끼지 않고 여기서 가져간다.
LABELS_BY_CODE: dict[str, tuple[str, ...]] = {
    code: tuple(sorted(label for label, mapped in _ENTITY_CODE.items() if mapped == code))
    for code in sorted(set(_ENTITY_CODE.values()))
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
    # 체크섬·지역코드·Luhn 같은 자체 검증을 통과했으면 1.0, 패턴만 맞으면 0.5,
    # 검증 수단이 없는 엔티티(이메일·IPv4·여권)는 None이다. MASKING 프로파일에서
    # 검증에 실패했지만 가리기 위해 남긴 span은 0.0이며, 신호로는 나가지 않는다.
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
# 경계는 숫자만 막는다. 한때 `-`와 영문까지 막아봤지만, 그러면
# `주민등록번호-901201-1234560`처럼 하이픈으로 이어 쓴 자연스러운 표기가
# 통째로 미탐이 됐다. 이 경계를 넓혔던 이유(UUID 꼬리가 면허번호로 잡힘)는
# 면허번호에 문맥 게이트가 생기면서 사라졌다.
_ID_BOUNDARY_BEFORE = r"(?<!\d)"
_ID_BOUNDARY_AFTER = r"(?!\d)"
_KR_RRN_RE = re.compile(
    _ID_BOUNDARY_BEFORE + r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-?[1-4]\d{6}" + _ID_BOUNDARY_AFTER
)
_KR_FRN_RE = re.compile(
    _ID_BOUNDARY_BEFORE + r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-?[5-8]\d{6}" + _ID_BOUNDARY_AFTER
)
# 여권: 구형 `M12345678`(영문1+숫자8), 신형 `M123A4567`(영문1+숫자3+영문1+숫자4).
# 예전 패턴 `[MR][A-Z]\d{7}`은 둘 중 어느 쪽도 아니어서 실제 여권번호를 하나도
# 잡지 못했다. 접두 문자도 M/R뿐이었으나 실제로는 M|S|R|O|D를 쓴다.
# 신형도 제품코드와 겹친다 -- `제품 M123A4567 재고 확인`이 KR_PASSPORT로 잡히는
# 것을 확인해, 구형과 같은 문맥 게이트를 건다(단독으로 써도 오탐이 거의 없다던
# 이전 판단은 틀렸다).
_KR_PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[MSRODmsrod]\d{3}[A-Za-z]\d{4}(?![0-9])")
# 구형(영문1+숫자8)은 제품코드·주문번호·리비전과 형태가 겹친다(`S24010203`,
# `D20260901`, `R12345678`). 그래서 근처에 여권을 가리키는 단어가 있을 때만
# 인정한다 -- Presidio도 같은 이유로 이 패턴에 0.05를 주고 context에 의존한다.
_KR_PASSPORT_LEGACY_RE = re.compile(r"(?<![A-Za-z0-9])[MSRODmsrod]\d{8}(?![0-9])")
_PASSPORT_CONTEXT_RE = re.compile(r"여권|passport", re.IGNORECASE)
_DRIVER_LICENSE_CONTEXT_RE = re.compile(r"면허|driver'?s? licen[sc]e", re.IGNORECASE)
_CONTEXT_WINDOW = 30
# 구분자는 하이픈·공백·없음 모두 허용한다. 지역번호는 아래에서 따로 검증한다.
_KR_DRIVER_LICENSE_RE = re.compile(
    _ID_BOUNDARY_BEFORE + r"\d{2}[- ]?\d{2}[- ]?\d{6}[- ]?\d{2}" + _ID_BOUNDARY_AFTER
)
_KR_DRIVER_LICENSE_REGIONS = frozenset(
    {"11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "28"}
)
# RFC 5321 local-part + domain — ASCII lookaround으로 한글/ASCII가 섞인 경계를 처리한다.
# 왼쪽 경계가 없으면 local-part의 `+`가 매 위치에서 남은 문자열을 통째로 삼켰다가
# `@`에서 실패하며 되짚어, 스캔 전체가 입력 길이의 제곱이 된다. 실측으로 연속된
# 영숫자 128,000자에서 13,820ms였고 경계를 붙이면 1.5ms다(탐지 결과는 동일).
# 경계는 토큰 시작 위치에서만 시도하게 만들 뿐 매치 범위를 바꾸지 않는다.
_EMAIL_RE = re.compile(r"(?<![a-zA-Z0-9._%+\-])[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# 전화번호는 정규식을 직접 쓰지 않고 Google libphonenumber의 파이썬 포팅에 맡긴다.
# 직접 쓴 패턴은 하이픈이 있는 표기만 잡아서 `01012345678`, `010 1234 5678`,
# `+82-10-1234-5678` 같은 흔한 형태를 놓쳤다. PhoneNumberMatcher는 원문 offset을
# 그대로 돌려주므로 EntitySpan에 바로 연결되고, 의존성이 없는 순수 파이썬이라
# "외부 모델 없이 결정적으로 동작한다"도 유지된다.
_PHONE_REGION = "KR"
# 옥텟을 0-255로 제한한다. 예전에는 `\d{1,3}`이라 `999.999.999.999`도 IP로 잡혔다.
_IP_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
_IP_ADDRESS_RE = re.compile(rf"(?<![\d.])(?:{_IP_OCTET}\.){{3}}{_IP_OCTET}(?![\d.])")
# IPv6. 문서와 Presidio의 IP_ADDRESS는 v4/v6를 모두 뜻하는데 v4만 보고 있었다.
# 후보만 느슨하게 뽑고 판정은 표준 ipaddress 모듈에 맡긴다 -- MAC 주소
# (`00:1B:44:11:3A:B7`)나 시각(`12:34:56`)은 여기서 걸러진다.
_IPV6_CANDIDATE_RE = re.compile(
    r"(?<![0-9A-Za-z:.])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![0-9A-Za-z:.])"
)
# 신용카드. Presidio의 credit_card_recognizer(MIT)와 같은 구조로, 발급사 접두를
# 강제하는 약한 정규식으로 후보를 좁히고 Luhn 체크섬으로 검증한다. Luhn만으로는
# 안 된다 -- 13자리 주민등록번호 `901201-1234560`이 Luhn을 통과하는 것을 확인했다.
_CREDIT_CARD_RE = re.compile(
    _ID_BOUNDARY_BEFORE + r"(?:"
    r"4\d{3}(?:[ -]?\d{4}){3}"                     # Visa 16
    r"|4\d{12}"                                    # Visa 13
    r"|5[1-5]\d{2}(?:[ -]?\d{4}){3}"               # Mastercard
    r"|2[2-7]\d{2}(?:[ -]?\d{4}){3}"               # Mastercard 2-series
    r"|3[47]\d{2}[ -]?\d{6}[ -]?\d{5}"             # American Express 15
    r"|6(?:011|5\d{2})(?:[ -]?\d{4}){3}"           # Discover
    r"|3(?:0[0-5]|[68]\d)\d[ -]?\d{6}[ -]?\d{4}"   # Diners Club 14
    r"|35\d{2}(?:[ -]?\d{4}){3}"                   # JCB
    r")" + _ID_BOUNDARY_AFTER
)
# `scheme://` 뒤의 authority 구간. RFC 3986의 authority는 첫 `/`, `?`, `#`,
# 공백에서 끝난다. 이 구간 안의 `userinfo@host`는 이메일 주소가 아니라 URI
# 자격증명이므로 EMAIL_ADDRESS로 잡으면 안 된다 -- 예전에는
# `postgresql://user:password@db.example.com`이 EMAIL_ADDRESS(D2)로 분류돼,
# secret detector가 같은 문자열에 붙인 DATABASE_URL(D5)을 strongest_code에서
# 가렸다. authority 밖(경로·쿼리·mailto:·평문)의 이메일은 그대로 탐지한다.
# scheme은 토큰 시작에서만 올 수 있다. 왼쪽 경계가 없으면 `_EMAIL_RE`와 같은
# 이유로 매 위치에서 남은 문자열을 삼켰다가 `://`에서 실패해 스캔이 제곱이 된다.
_URI_AUTHORITY_RE = re.compile(r"(?<![a-zA-Z0-9+.\-])[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/?#]*")

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
    """지역번호(앞 2자리)가 실제 발급 지역이 아니면 탐지에서 제외한다.

    신뢰도는 0.5다. 면허번호 11번째 자리는 검증번호지만 산출식이 공개돼 있지
    않아 재계산할 수단이 없고, 남는 검사는 지역코드 열거뿐이다. 무작위 12자리
    숫자를 30만 개 넣어보니 17%가 이 지역코드를 통과했다 -- 체크섬으로 걸러낸
    주민등록번호(0.16%)와 같은 1.0을 줄 근거가 없다. Presidio도 이 패턴에
    가장 낮은 0.05를 주고 문맥에 의존한다.
    """
    digits = re.sub(r"[- ]", "", matched)
    if len(digits) != 12 or digits[:2] not in _KR_DRIVER_LICENSE_REGIONS:
        return None
    return 0.5


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _credit_card_confidence(matched: str) -> float | None:
    """Luhn 체크섬을 통과하지 못한 카드번호 후보는 제외한다."""
    digits = re.sub(r"[ -]", "", matched)
    if not 13 <= len(digits) <= 19 or not _luhn_ok(digits):
        return None
    return 1.0


def _is_not_an_exposed_ip(matched: str) -> bool:
    """IP로 파싱되지 않거나, 노출로서 의미가 없는 특수 주소인지 본다.

    저장소 텍스트에서 이 규칙이 잡은 148건을 분류해보니 `0.0.0.0` 83건,
    `127.x` 56건, 사설 대역 9건이고 공인 IP는 하나도 없었다. bind 주소와
    loopback은 "인프라 노출"이라는 D5의 뜻과 아무 관계가 없다. 사설 대역은
    내부 토폴로지를 드러내므로 남긴다.

    이건 검증기가 아니라 억제기다 -- 검증 실패(체크섬 불일치)는 "맞는데 확인이
    안 된 값"이라 마스킹은 가려야 하지만, `0.0.0.0`은 어느 프로파일에서도
    가릴 대상이 아니다. 로그에서 bind 주소가 지워지면 그게 더 손해다.
    """
    try:
        address = ipaddress.ip_address(matched)
    except ValueError:
        return True
    return bool(
        address.is_unspecified
        or address.is_loopback
        or address.is_multicast
        or address.is_reserved
        or address.is_link_local
    )


# 인식기 하나는 (엔티티, 패턴, 억제기, 검증기)다.
#  - 억제기가 True면 그 값은 애초에 이 엔티티가 아니다. 두 프로파일 모두 버린다.
#  - 검증기가 None을 돌려주면 모양은 맞는데 확인에 실패한 것이다. SIGNAL은
#    버리고 MASKING은 남겨 가린다.
Validator = Callable[[str], float | None]
Suppressor = Callable[[str], bool]
_RECOGNIZERS: tuple[tuple[str, re.Pattern[str], Suppressor | None, Validator | None], ...] = (
    ("KR_RRN", _KR_RRN_RE, None, _kr_registration_confidence),
    ("KR_FRN", _KR_FRN_RE, None, _kr_registration_confidence),
    ("KR_PASSPORT_MODERN", _KR_PASSPORT_RE, None, None),
    ("KR_PASSPORT_LEGACY", _KR_PASSPORT_LEGACY_RE, None, None),
    ("KR_DRIVER_LICENSE_CANDIDATE", _KR_DRIVER_LICENSE_RE, None, _kr_driver_license_confidence),
    ("CREDIT_CARD", _CREDIT_CARD_RE, None, _credit_card_confidence),
    ("IP_ADDRESS", _IP_ADDRESS_RE, _is_not_an_exposed_ip, None),
    ("IP_ADDRESS", _IPV6_CANDIDATE_RE, _is_not_an_exposed_ip, None),
    ("EMAIL_ADDRESS", _EMAIL_RE, None, None),
)
# 모양만으로는 판별력이 없어 근처 낱말이 유일한 판별자인 엔티티들이다.
# 후보 엔티티 -> (승격될 엔티티, 문맥 패턴).
_CONTEXT_GATES: dict[str, tuple[str, re.Pattern[str]]] = {
    "KR_PASSPORT_MODERN": ("KR_PASSPORT", _PASSPORT_CONTEXT_RE),
    "KR_PASSPORT_LEGACY": ("KR_PASSPORT", _PASSPORT_CONTEXT_RE),
    "KR_DRIVER_LICENSE_CANDIDATE": ("KR_DRIVER_LICENSE", _DRIVER_LICENSE_CONTEXT_RE),
}
# 문맥이 없어도 마스킹은 남기는 후보. 모양이 충분히 특이해 우연히 겹치는 값이
# 드문 것만 넣는다. 무작위 값이 이 모양에 걸릴 확률을 재보면 신형 여권도
# 19%지만, 그 모양(영문1+숫자3+영문1+숫자4)으로 된 값이 실제 문서에 등장하는
# 빈도가 12자리 숫자나 `[MSROD]`+숫자8과 비교가 안 되게 낮다.
_MASK_WITHOUT_CONTEXT = frozenset({"KR_PASSPORT_MODERN"})


def _regex_spans(
    text: str,
    entity: str,
    pattern: re.Pattern[str],
    suppressor: Suppressor | None,
    validator: Validator | None,
    profile: DetectionProfile,
) -> list[EntitySpan]:
    spans: list[EntitySpan] = []
    for match in pattern.finditer(text):
        if suppressor is not None and suppressor(match.group(0)):
            continue
        confidence = validator(match.group(0)) if validator is not None else None
        if validator is not None and confidence is None:
            if profile is DetectionProfile.SIGNAL:
                continue
            # 마스킹은 검증에 실패한 후보도 가린다. 지역번호가 틀린 면허번호를
            # 로그에 평문으로 남기는 손해가, 사번 하나를 가리는 손해보다 크다.
            confidence = 0.0
        spans.append(EntitySpan(entity, match.start(), match.end(), confidence))
    return spans


def _run_custom_span_recognizers(text: str, profile: DetectionProfile) -> list[EntitySpan]:
    """신뢰도 높은 local recognizer 결과와 문자 offset을 반환한다.

    외부 모델이나 cache I/O 없이 결정적으로 동작한다.
    """
    spans: list[EntitySpan] = []
    for entity, pattern, suppressor, validator in _RECOGNIZERS:
        spans.extend(_regex_spans(text, entity, pattern, suppressor, validator, profile))
    spans.extend(_phone_spans(text))
    spans = _context_gated_spans(text, spans, profile)

    return _drop_email_spans_inside_uri_authority(text, spans)


def _context_gated_spans(
    text: str,
    spans: list[EntitySpan],
    profile: DetectionProfile,
) -> list[EntitySpan]:
    """문맥 단어가 근처에 있을 때만 후보를 정식 엔티티로 승격한다.

    여권(구형 `M12345678`, 신형 `M123A4567`)과 운전면허번호는 모양만으로는
    제품코드·주문번호·일련번호와 갈라지지 않는다. 무작위 값을 30만 개씩 넣어
    재보면 12자리 숫자의 17%가 면허 지역코드를, `[MSROD]`+숫자8의 19%가 여권
    구형 패턴을 통과한다. 즉 패턴 자체에는 판별력이 없고 근처 낱말이 판별자다.
    Presidio가 이 패턴들에 0.05~0.1을 주고 context에 의존하는 것과 같은 이유다.

    마스킹에서 문맥을 면제하는 기준은 "검증에 실패했더라도 근거가 있었는가"이지
    "모양이 스치기만 했는가"가 아니다. 그래서 면제 대상은 신형 여권뿐이다 --
    문맥 없이 마스킹하면 로그의 주문번호와 면허 모양 숫자가 통째로 지워진다.
    """
    kept: list[EntitySpan] = []
    for span in spans:
        gate = _CONTEXT_GATES.get(span.entity)
        if gate is None:
            kept.append(span)
            continue
        promoted, context_re = gate
        window = text[max(0, span.start - _CONTEXT_WINDOW) : span.end + _CONTEXT_WINDOW]
        # 문맥 낱말은 한글이라 NFC로 합성해야 매치된다. 인식 단계의 정규화는
        # offset을 되돌리려고 문자 단위 NFKC만 쓰는데, 그건 자모를 음절로
        # 합성하지 못한다 -- macOS에서 붙여넣은 NFD 텍스트에서는 `여권`·`면허`가
        # 자모로 흩어져 게이트가 통째로 닫혔다. 여기는 boolean 판정뿐이라
        # offset과 무관하게 문자열 전체를 NFC로 정규화해도 안전하다.
        if not context_re.search(unicodedata.normalize("NFC", window)):
            if profile is DetectionProfile.SIGNAL or span.entity not in _MASK_WITHOUT_CONTEXT:
                continue
        kept.append(EntitySpan(promoted, span.start, span.end, span.confidence))
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


def _recognize(text: str, profile: DetectionProfile) -> list[EntitySpan]:
    """정규화한 텍스트에서 인식한 뒤 span을 원문 offset으로 되돌린다.

    인식은 NFKC로 접고 서식 제어문자를 걷어낸 텍스트에서 한다. 그래야 전각
    숫자로 쓴 주민등록번호나 숫자 사이에 zero-width space를 끼워 넣은 표기가
    끊기지 않는다. 반환 span은 다시 원문 기준이라 마스킹이 원문을 그대로
    잘라 붙일 수 있다.
    """
    normalized, origin = normalize_with_origin(text)
    spans = _run_custom_span_recognizers(normalized, profile)
    if origin is None:
        return spans
    mapped: list[EntitySpan] = []
    for span in spans:
        start, end = to_origin_span(origin, span.start, span.end)
        mapped.append(EntitySpan(span.entity, start, end, span.confidence))
    return mapped


def mask_pii(text: str) -> str:
    """탐지된 PII span을 라벨로 치환한 텍스트를 반환한다.

    assess()와 달리 원문 offset을 실제로 텍스트에 적용한다 -- 디버그 로깅처럼
    사람이 읽을 원문이 필요한 소비처 전용이며, risk 판정 경로(assess)와는 분리된다.
    검증에 실패한 후보까지 가리는 MASKING 프로파일로 동작한다.
    """
    spans = _reconcile_spans(_recognize(text, DetectionProfile.MASKING))
    for span in sorted(spans, key=lambda item: item.start, reverse=True):
        text = text[: span.start] + f"[{span.entity}]" + text[span.end :]
    return text


class PIIProtectionDetector:
    """인식, 기술적 중복 제거, 분류, 응답 생성을 순서대로 조율한다.

    Each stage is implemented separately. The detector does not validate whether
    an identifier exists or belongs to a real person or business.
    """

    async def assess(self, text: str) -> dict[str, Any]:
        recognized = _recognize(text, DetectionProfile.SIGNAL)
        reconciled = _reconcile_spans(recognized)
        summaries = _classify_spans(reconciled)
        categories = _categories_from_summaries(summaries)
        return _build_assessment(categories)
