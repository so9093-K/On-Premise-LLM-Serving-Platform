from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from ..risk import assessment_response

SOURCE_MODEL = "secret-scanner"

# --------------------------------------------------------------------------
# Regex patterns: (compiled_re, entity_label)
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
    # OpenAI API key
    (re.compile(r"sk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}"), "OPENAI_API_KEY"),
    # OpenAI new-format key (sk-proj-, sk-svcacct-)
    (re.compile(r"sk-(?:proj|svcacct)-[A-Za-z0-9_\-]{30,}"), "OPENAI_API_KEY"),
    # Anthropic / Claude API key (sk-ant-api03-...)
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"), "ANTHROPIC_API_KEY"),
    # AWS Access Key ID
    (re.compile(r"(?<![A-Z0-9])(AKIA[0-9A-Z]{16})(?![A-Z0-9])"), "AWS_ACCESS_KEY_ID"),
    # GitHub personal access token (classic: ghp_, fine-grained: github_pat_)
    (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}"), "GITHUB_TOKEN"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{82}"), "GITHUB_TOKEN"),
    # GitLab personal/group/project token
    (re.compile(r"glpat-[A-Za-z0-9\-_]{20}"), "GITLAB_TOKEN"),
    # HuggingFace token
    (re.compile(r"hf_[A-Za-z0-9]{34}"), "HUGGINGFACE_TOKEN"),
    # JWT: three base64url segments separated by dots
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_\-=+/]{10,}"), "JWT"),
    # PEM private key block
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "PRIVATE_KEY_BLOCK"),
    # Database connection URL
    (re.compile(
        r"(?:mysql|postgresql|postgres|mongodb(?:\+srv)?|redis|amqp|amqps|mariadb)://"
        r"[A-Za-z0-9_.%~!$&'()*+,;=:@\-]+:[A-Za-z0-9_.%~!$&'()*+,;=:@\-]+@"
        r"[A-Za-z0-9.\-]+"
    ), "DATABASE_URL"),
    # Password assignment patterns
    (re.compile(
        r'(?i)(?:password|passwd|secret|api[_\-]?key|token|auth[_\-]?token)\s*[:=]\s*'
        r'(?!["\']?\s*["\'])'      # not empty value
        r'(["\']?)([^\s,;\'"]{6,})(\1)'
    ), "PASSWORD_ASSIGNMENT"),
]

# High-entropy generic secret candidate
_GENERIC_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
_GENERIC_ENTROPY_THRESHOLD = 4.5


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _find_generic_candidates(text: str) -> int:
    """Count high-entropy strings that aren't matched by named patterns."""
    # Remove known-pattern matches to avoid double-counting
    cleaned = text
    for pattern, _ in _PATTERNS:
        cleaned = pattern.sub("", cleaned)

    count = 0
    for match in _GENERIC_CANDIDATE_RE.finditer(cleaned):
        candidate = match.group(0)
        if candidate in _ALLOWLIST_GENERIC:
            continue
        if _shannon_entropy(candidate) >= _GENERIC_ENTROPY_THRESHOLD:
            count += 1
    return count


def _scan_text(text: str) -> dict[str, int]:
    """Scan text for secrets; return entity_label -> span_count, without raw values."""
    counts: dict[str, int] = {}

    for pattern, label in _PATTERNS:
        matches = pattern.findall(text)
        if matches:
            counts[label] = counts.get(label, 0) + len(matches)

    generic = _find_generic_candidates(text)
    if generic > 0:
        counts["GENERIC_SECRET_CANDIDATE"] = counts.get("GENERIC_SECRET_CANDIDATE", 0) + generic

    return counts

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
    """Secret/credential exposure detector using curated regex, entropy, and context keywords.

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
