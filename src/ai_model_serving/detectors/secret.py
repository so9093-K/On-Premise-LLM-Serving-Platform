from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..risk import assessment_response
from .filters import is_placeholder, is_structural
from .normalization import normalize_with_origin, to_origin_span

SOURCE_MODEL = "secret-scanner"


@dataclass(frozen=True)
class SecretRule:
    """정규식 하나와 그 규칙이 실제 시크릿으로 보는 범위를 함께 담는다.

    ``value_groups``는 Gitleaks의 secretGroup과 같은 역할이다. 대입문에서는
    매치 전체가 `password="hunter2"`이지만 시크릿 본체는 `hunter2`뿐이라,
    자리표시자 판정과 마스킹 범위를 매치 전체와 분리해야 한다. 비어 있으면
    매치 전체가 곧 본체다(벤더 토큰처럼 값만 매치되는 규칙).
    """

    label: str
    pattern: re.Pattern[str]
    value_groups: tuple[str, ...] = ()
    # 값의 모양이 아니라 구조 표지(PEM 헤더, URI scheme+userinfo)로 판정하는
    # 규칙인지. 이런 규칙에는 자리표시자 필터를 걸지 않는다 -- 무엇이 비밀인지
    # 이미 표지가 증명했고, 본문 모양으로 다시 거르면 미탐만 만든다.
    structural_marker: bool = False
    # 문맥 없이 모양만으로 잡는 규칙인지. 이런 규칙만 구조물 필터(경로·해시·
    # 인코딩된 미디어)를 통과해야 한다. 대입 키워드나 헤더 이름이 앞에 붙은
    # 값은 경로처럼 생겼어도 자격증명으로 본다.
    context_free: bool = False


# --------------------------------------------------------------------------
# 대입·헤더 규칙 (문맥이 판별자)
# --------------------------------------------------------------------------

# 복수형(`s?`)을 허용한다. 없을 때는 `API_KEYS=`, `ADMIN_API_KEYS=`,
# `SECRETS=`, `CREDENTIALS=`가 전부 미탐이었다 -- 이 저장소의 .env가 쓰는
# 이름들이다.
#
# 다만 맨 `token`은 복수형에서 제외한다. LLM 서빙에서 `tokens`는 자격증명이
# 아니라 개수다(`max_tokens`, `prompt_tokens`, `max_num_batched_tokens`).
# 복수형을 붙였더니 이 저장소에서만 오탐이 수십 건 생겼다. 키워드는 그 낱말
# 자체가 비밀을 뜻해야 하고, 비밀을 담곤 하는 개념이면 안 된다는 기준을
# `session`에 이어 여기에도 적용한다.
_ASSIGNMENT_KEYWORD = (
    r"(?:token|(?:password|passwd|pwd|secret|api[_\-]?key|access[_\-]?key|secret[_\-]?key|"
    r"auth[_\-]?token|access[_\-]?token|refresh[_\-]?token|credential|"
    r"private[_\-]?key|client[_\-]?secret|connection[_\-]?string|"
    # 세션 식별자도 그 자체가 자격증명이고, 16진수라 모양만으로는 커밋 해시와
    # 구분되지 않아 문맥이 유일한 판별자다. 다만 접미어를 요구한다 -- 맨
    # `session`은 평범한 산문에도 나와서(`session: 30분입니다`) 키워드로 쓸 수
    # 없다. 키워드는 그 낱말 자체가 비밀을 뜻해야 하고, 비밀을 담곤 하는
    # 개념이면 안 된다.
    r"session[_\-]?(?:id|key|secret|token))s?)"
)
# 값의 인용 형태를 분기해서 각각 캡처한다.
#
# 예전 패턴은 빈 값을 거르려고 `(?!["\']?\s*["\'])` lookahead를 뒀는데,
# `["\']?`가 빈 문자열에도 매치되는 탓에 lookahead가 "(없음)+(공백0)+따옴표"로
# 성립해 **따옴표로 시작하는 모든 값**을 탈락시켰다. JSON·YAML·dotenv처럼
# 값을 인용하는 표기가 전부 미탐이었고, 뒤따르던 따옴표 캡처와 역참조 `\1`은
# 도달하지 못하는 죽은 코드였다. 빈 값은 `{6,}` 수량자가 이미 거르므로
# lookahead 자체를 없애고 인용 형태를 명시적으로 갈랐다.
_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])" + _ASSIGNMENT_KEYWORD + r"[\"']?\s*[:=]\s*"
    r"(?:\"(?P<dq>[^\"\r\n]{6,}?)\""
    r"|'(?P<sq>[^'\r\n]{6,}?)'"
    r"|(?P<bare>[^\s,;\"']{6,}))"
)
# `Authorization: Basic <base64>` / `Bearer <token>`.
# 헤더 이름은 `auth[_-]?token` 같은 대입 키워드에 걸리지 않고, 값도 16진수나
# base64라 엔트로피 임계값을 넘지 못해 통째로 빠져 있었다.
# `X-API-Key`류 헤더는 대입 규칙의 `api[_-]?key` 키워드가 이미 가져가므로 여기
# 넣지 않는다. 규칙 둘이 같은 값을 잡으면 reconcile이 하나를 버릴 뿐이고,
# 어느 라벨이 붙을지는 규칙 순서라는 우연에 달리게 된다.
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(?:proxy-)?authorization\s*:\s*"
    r"(?:basic|bearer|token)?\s*(?P<value>[A-Za-z0-9._~+/=\-]{8,})"
)

# --------------------------------------------------------------------------
# 벤더 토큰 규칙 (모양이 곧 판별자)
# --------------------------------------------------------------------------

_VENDOR_PATTERNS: list[tuple[str, str]] = [
    # OpenAI
    ("OPENAI_API_KEY", r"sk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}"),
    ("OPENAI_API_KEY", r"sk-(?:proj|svcacct)-[A-Za-z0-9_\-]{30,}"),
    # Anthropic / Claude
    ("ANTHROPIC_API_KEY", r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    # AWS 액세스 키 ID
    ("AWS_ACCESS_KEY_ID", r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    # GitHub (classic / fine-grained)
    ("GITHUB_TOKEN", r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
    ("GITHUB_TOKEN", r"github_pat_[A-Za-z0-9_]{82}(?![A-Za-z0-9_])"),
    # GitLab
    ("GITLAB_TOKEN", r"glpat-[A-Za-z0-9\-_]{20}(?![A-Za-z0-9\-_])"),
    # HuggingFace
    ("HUGGINGFACE_TOKEN", r"hf_[A-Za-z0-9]{34}(?![A-Za-z0-9])"),
    # Slack (bot/user/app/refresh)
    ("SLACK_TOKEN", r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    # Stripe
    ("STRIPE_KEY", r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"),
    # Google API 키
    ("GOOGLE_API_KEY", r"AIza[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])"),
    # npm 액세스 토큰
    ("NPM_TOKEN", r"npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
    # SendGrid
    ("SENDGRID_KEY", r"SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
    # JWT: 점으로 구분된 세 개의 base64url 세그먼트
    ("JWT", r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_\-=+/]{10,}"),
]

# PEM/PGP 개인키. 예전에는 BEGIN 헤더 한 줄만 매치해서, 마스킹이 헤더만 지우고
# **키 본문은 로그에 평문으로 남겼다**. 블록 전체를 범위로 잡는다.
_PRIVATE_KEY_HEADER = (
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----"
)
# END를 못 찾으면 헤더만 잡던 예전 분기는 본문을 평문으로 남겼다. 프리뷰 로깅이
# 입력을 잘라 END가 떨어져 나가는 경우가 실제로 발생한다. 키 블록이 열렸는데
# 닫히지 않았다면 뒤따르는 내용이 전부 키일 수 있으므로 fail-closed로 잡는다
# (한 번만 매치되므로 스캔 비용은 선형이다).
_PRIVATE_KEY_BODY = (
    r"[\s\S]{0,8000}?-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?"
    r"PRIVATE KEY(?: BLOCK)?-----"
)
# 종료 태그가 없을 때의 대체 본문. 아무 문자나 삼키면 산문 속에서 헤더를 언급한
# 줄의 뒷부분까지 통째로 지워지므로, PEM 본문의 실제 모양인 base64 줄만 먹는다.
# 두 문자 클래스가 겹치지 않아 되짚기가 폭발하지 않는다.
_PRIVATE_KEY_UNTERMINATED_BODY = r"(?:[\r\n\t ]*[A-Za-z0-9+/=]{1,80}){0,200}"
_PRIVATE_KEY_RE = re.compile(
    _PRIVATE_KEY_HEADER + r"(?:" + _PRIVATE_KEY_BODY + r"|" + _PRIVATE_KEY_UNTERMINATED_BODY + r")"
)

_DATABASE_URL_RE = re.compile(
    r"(?:mysql|postgresql|postgres|mongodb(?:\+srv)?|redis|amqp|amqps|mariadb)://"
    # 사용자 부분은 비어 있을 수 있다(`redis://:secret@host`).
    r"[A-Za-z0-9_.%~!$&'()*+,;=:@\-]*:[A-Za-z0-9_.%~!$&'()*+,;=:@\-]+@"
    r"[A-Za-z0-9.\-]+"
)

# 고엔트로피 일반 시크릿 후보.
# `=`는 base64 패딩이라 뒤에만 올 수 있는데 예전 문자 클래스는 중간에도 허용해
# `PLATFORM_IMAGE=ai-model-serving-platform` 같은 대입문을 통째로 삼켰다.
_GENERIC_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/_\-]{32,}={0,2}")
# 엔트로피 임계값은 4.5를 유지한다. 이 저장소 텍스트에서 32자 이상 후보 956개를
# 뽑아 재보니 4.0으로 내렸을 때 새로 걸리는 139개가 전부 파일 경로·테스트 이름·
# 환경변수였다. 재현율은 엔트로피가 아니라 문맥 규칙(대입·헤더)으로 올린다.
_GENERIC_ENTROPY_THRESHOLD = 4.5

# 이 배열의 순서가 곧 겹칠 때의 우선순위다(_reconcile 참고). 구체적인 규칙일수록
# 앞에 둔다 -- 벤더 토큰 > 구조 표지 > 대입/헤더. 순서를 바꾸면 `GH_TOKEN=ghp_...`
# 같은 입력의 라벨이 조용히 바뀐다.
_RULES: list[SecretRule] = [
    *(
        SecretRule(label=label, pattern=re.compile(pattern), context_free=True)
        for label, pattern in _VENDOR_PATTERNS
    ),
    SecretRule(label="PRIVATE_KEY_BLOCK", pattern=_PRIVATE_KEY_RE, context_free=True, structural_marker=True),
    SecretRule(label="DATABASE_URL", pattern=_DATABASE_URL_RE, context_free=True, structural_marker=True),
    SecretRule(
        label="PASSWORD_ASSIGNMENT",
        pattern=_ASSIGNMENT_RE,
        value_groups=("dq", "sq", "bare"),
    ),
    SecretRule(label="AUTH_HEADER", pattern=_AUTH_HEADER_RE, value_groups=("value",)),
]

_LABEL_CODE: dict[str, str] = {
    "OPENAI_API_KEY": "D4",
    "ANTHROPIC_API_KEY": "D4",
    "AWS_ACCESS_KEY_ID": "D4",
    "GITHUB_TOKEN": "D4",
    "GITLAB_TOKEN": "D4",
    "HUGGINGFACE_TOKEN": "D4",
    "SLACK_TOKEN": "D4",
    "STRIPE_KEY": "D4",
    "GOOGLE_API_KEY": "D4",
    "NPM_TOKEN": "D4",
    "SENDGRID_KEY": "D4",
    "JWT": "D4",
    "PRIVATE_KEY_BLOCK": "D4",
    "PASSWORD_ASSIGNMENT": "D4",
    "AUTH_HEADER": "D4",
    "GENERIC_SECRET_CANDIDATE": "D4",
    "DATABASE_URL": "D5",
}

# 공개 문서(endpoint_spec)가 라벨 목록을 손으로 베끼지 않고 여기서 가져간다.
# 예전에는 설명 문자열에 라벨을 직접 적어둬서 ANTHROPIC_API_KEY가 추가됐을 때
# 문서 두 곳이 그대로 뒤처졌다.
LABELS_BY_CODE: dict[str, tuple[str, ...]] = {
    code: tuple(sorted(label for label, mapped in _LABEL_CODE.items() if mapped == code))
    for code in sorted(set(_LABEL_CODE.values()))
}


def _shannon_entropy(s: str) -> float:
    # 빈 문자열 가드는 두지 않는다: 호출자는 _GENERIC_CANDIDATE_RE({32,}) 매치만
    # 넘기므로 도달하지 않고, 도달하더라도 freq가 비어 아래 합이 그대로 0이 된다.
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _value_span(match: re.Match[str], rule: SecretRule) -> tuple[int, int, str] | None:
    """규칙이 시크릿 본체로 보는 구간과 그 값을 돌려준다."""
    if not rule.value_groups:
        return match.start(), match.end(), match.group(0)
    for name in rule.value_groups:
        if match.group(name) is not None:
            return match.start(name), match.end(name), match.group(name)
    return None


def _rule_spans(text: str) -> list[tuple[int, int, str, int]]:
    """규칙 순서를 우선순위로 함께 실어 반환한다(낮을수록 구체적인 규칙)."""
    spans: list[tuple[int, int, str, int]] = []
    for priority, rule in enumerate(_RULES):
        for match in rule.pattern.finditer(text):
            resolved = _value_span(match, rule)
            if resolved is None:
                continue
            start, end, value = resolved
            # 구조 표지로 판정하는 규칙은 값 모양 필터를 전부 건너뛴다. PEM 블록은
            # 길이 상한(데이터 blob을 거르려고 둔 것)에 걸려 통째로 버려졌다.
            if not rule.structural_marker:
                if is_placeholder(value):
                    continue
                if rule.context_free and is_structural(value):
                    continue
            spans.append((start, end, rule.label, priority))
    return spans


def _reconcile(spans: list[tuple[int, int, str, int]]) -> list[tuple[int, int, str]]:
    """같은 노출이 규칙 여러 개에 걸렸을 때 가장 구체적인 규칙만 남긴다.

    `GH_TOKEN=ghp_...`는 벤더 규칙과 대입 규칙에 동시에 걸려 span_count가 두 배가
    되고, 마스킹은 두 span을 같은 구간에 겹쳐 치환하다 더 구체적인 라벨을 잃었다.
    겹치면 규칙 순서가 앞선 쪽(벤더 > 대입/헤더 > generic)을 남긴다.
    """
    kept: list[tuple[int, int, str]] = []
    for start, end, label, _ in sorted(
        spans, key=lambda span: (span[3], span[0], -(span[1] - span[0]))
    ):
        if any(start < other_end and end > other_start for other_start, other_end, _ in kept):
            continue
        kept.append((start, end, label))
    return kept


def _generic_spans(text: str, taken: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in _GENERIC_CANDIDATE_RE.finditer(text):
        start, end = match.start(), match.end()
        if any(start < other_end and end > other_start for other_start, other_end in taken):
            continue  # 기명 규칙과 겹치는 구간은 중복 집계하지 않는다
        candidate = match.group(0)
        if is_placeholder(candidate) or is_structural(candidate):
            continue
        if _shannon_entropy(candidate) >= _GENERIC_ENTROPY_THRESHOLD:
            spans.append((start, end, "GENERIC_SECRET_CANDIDATE"))
    return spans


def _scan_spans(text: str) -> list[tuple[int, int, str]]:
    """원문 기준 offset으로 모든 secret span의 ``(시작, 끝, 라벨)``을 반환한다."""
    normalized, origin = normalize_with_origin(text)
    spans = _reconcile(_rule_spans(normalized))
    spans.extend(_generic_spans(normalized, [(start, end) for start, end, _ in spans]))
    if origin is None:
        return spans
    return [(*to_origin_span(origin, start, end), label) for start, end, label in spans]


def _scan_text(text: str) -> dict[str, int]:
    """원문 값 없이 secret을 검사해 ``entity_label → span_count``를 반환한다."""
    counts: dict[str, int] = {}
    for _, _, label in _scan_spans(text):
        counts[label] = counts.get(label, 0) + 1
    return counts


def mask_secrets(text: str) -> str:
    """탐지된 secret span을 라벨로 치환한 텍스트를 반환한다(디버그 로깅 전용).

    대입·헤더 규칙은 값 구간만 스팬으로 잡으므로 `password=[PASSWORD_ASSIGNMENT]`
    처럼 어떤 키가 노출됐는지는 로그에 남는다.
    """
    for start, end, label in sorted(_scan_spans(text), key=lambda item: item[0], reverse=True):
        text = text[:start] + f"[{label}]" + text[end:]
    return text


def _build_categories(entity_counts: dict[str, int]) -> list[dict[str, Any]]:
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
    for label, span_count in sorted(entity_counts.items()):
        code = _LABEL_CODE[label]
        categories.append(
            {
                "code": code,
                "family": "data_exposure",
                "detected": True,
                "confidence": None,
                "source_model": SOURCE_MODEL,
                "label": label,
                "span_count": span_count,
            }
        )
    return categories


class SecretExposureDetector:
    """선별한 정규식, entropy, 문맥 키워드로 secret·credential 노출을 탐지한다.

    No external CLI tools (Gitleaks, TruffleHog) are invoked at request time.
    Original secret values are never included in the response.
    """

    async def assess(self, text: str) -> dict[str, Any]:
        entity_counts = _scan_text(text)
        categories = _build_categories(entity_counts)
        detected = any(c["detected"] for c in categories)
        message = "Secret exposure signal detected." if detected else "No secret signal detected."
        return assessment_response(
            categories=categories,
            system_signals=[],
            status="completed",
            message=message,
        )
