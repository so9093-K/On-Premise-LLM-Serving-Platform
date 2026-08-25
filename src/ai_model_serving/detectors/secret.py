from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from ..risk import assessment_response

SOURCE_MODEL = "secret-scanner"

# --------------------------------------------------------------------------
# 정규식 패턴: (compiled_re, entity_label)
# --------------------------------------------------------------------------

_ALLOWLIST_GENERIC: frozenset[str] = frozenset(
    {
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "0000000000000000000000000000000000000000",
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "1234567890abcdef1234567890abcdef1234567890ab",
        "your_secret_here",
        "your_api_key_here",
        "replace_me",
        "<your-token>",
        "<api-key>",
    }
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI API 키
    (re.compile(r"sk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}"), "OPENAI_API_KEY"),
    # OpenAI 신규 포맷 키 (sk-proj-, sk-svcacct-)
    (re.compile(r"sk-(?:proj|svcacct)-[A-Za-z0-9_\-]{30,}"), "OPENAI_API_KEY"),
    # Anthropic / Claude API 키 (sk-ant-api03-...)
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"), "ANTHROPIC_API_KEY"),
    # AWS 액세스 키 ID
    (re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])"), "AWS_ACCESS_KEY_ID"),
    # GitHub 개인 액세스 토큰 (classic: ghp_, fine-grained: github_pat_)
    (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}"), "GITHUB_TOKEN"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{82}"), "GITHUB_TOKEN"),
    # GitLab personal/group/project 토큰
    (re.compile(r"glpat-[A-Za-z0-9\-_]{20}"), "GITLAB_TOKEN"),
    # HuggingFace 토큰
    (re.compile(r"hf_[A-Za-z0-9]{34}"), "HUGGINGFACE_TOKEN"),
    # JWT: 점으로 구분된 세 개의 base64url 세그먼트
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_\-=+/]{10,}"), "JWT"),
    # PEM 개인키 블록
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "PRIVATE_KEY_BLOCK"),
    # 데이터베이스 연결 URL
    (re.compile(
        r"(?:mysql|postgresql|postgres|mongodb(?:\+srv)?|redis|amqp|amqps|mariadb)://"
        r"[A-Za-z0-9_.%~!$&'()*+,;=:@\-]+:[A-Za-z0-9_.%~!$&'()*+,;=:@\-]+@"
        r"[A-Za-z0-9.\-]+"
    ), "DATABASE_URL"),
    # 비밀번호 할당 패턴
    (re.compile(
        r'(?i)(?:password|passwd|secret|api[_\-]?key|token|auth[_\-]?token)\s*[:=]\s*'
        r'(?!["\']?\s*["\'])'      # 값이 비어있지 않음
        r'(["\']?)([^\s,;\'"]{6,})(\1)'
    ), "PASSWORD_ASSIGNMENT"),
]

# 고엔트로피 일반 시크릿 후보
_GENERIC_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
_GENERIC_ENTROPY_THRESHOLD = 4.5


def _shannon_entropy(s: str) -> float:
    # 빈 문자열 가드는 두지 않는다: 호출자는 _GENERIC_CANDIDATE_RE({32,}) 매치만
    # 넘기므로 도달하지 않고, 도달하더라도 freq가 비어 아래 합이 그대로 0이 된다.
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _scan_spans(text: str) -> list[tuple[int, int, str]]:
    """원문 기준 offset으로 모든 secret span의 ``(시작, 끝, 라벨)``을 반환한다."""
    spans: list[tuple[int, int, str]] = []
    for pattern, label in _PATTERNS:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), label))

    named_ranges = [(start, end) for start, end, _ in spans]
    for match in _GENERIC_CANDIDATE_RE.finditer(text):
        start, end = match.start(), match.end()
        if any(start < named_end and end > named_start for named_start, named_end in named_ranges):
            continue  # 기명 패턴과 겹치는 구간은 중복 집계하지 않는다
        candidate = match.group(0)
        if candidate in _ALLOWLIST_GENERIC:
            continue
        if _shannon_entropy(candidate) >= _GENERIC_ENTROPY_THRESHOLD:
            spans.append((start, end, "GENERIC_SECRET_CANDIDATE"))
    return spans


def _scan_text(text: str) -> dict[str, int]:
    """원문 값 없이 secret을 검사해 ``entity_label → span_count``를 반환한다."""
    counts: dict[str, int] = {}
    for _, _, label in _scan_spans(text):
        counts[label] = counts.get(label, 0) + 1
    return counts


def mask_secrets(text: str) -> str:
    """탐지된 secret span을 라벨로 치환한 텍스트를 반환한다(디버그 로깅 전용)."""
    for start, end, label in sorted(_scan_spans(text), key=lambda item: item[0], reverse=True):
        text = text[:start] + f"[{label}]" + text[end:]
    return text

_LABEL_CODE: dict[str, str] = {
    "OPENAI_API_KEY": "D4",
    "ANTHROPIC_API_KEY": "D4",
    "AWS_ACCESS_KEY_ID": "D4",
    "GITHUB_TOKEN": "D4",
    "GITLAB_TOKEN": "D4",
    "HUGGINGFACE_TOKEN": "D4",
    "JWT": "D4",
    "PRIVATE_KEY_BLOCK": "D4",
    "PASSWORD_ASSIGNMENT": "D4",
    "GENERIC_SECRET_CANDIDATE": "D4",
    "DATABASE_URL": "D5",
}


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
