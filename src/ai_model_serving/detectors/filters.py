from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 시크릿 후보 억제 필터
#
# 예전에는 문자열 10개짜리 하드코딩 알로우리스트(`_ALLOWLIST_GENERIC`)로 걸렀다.
# 목록에 없는 형태(`***`, `<REDACTED>`, `${VAR}`, `changeme`)는 그대로 통과했고,
# 새 형태를 만날 때마다 문자열을 덧붙여야 했다. 값 하나가 아니라 "모양"을 거르는
# 규칙으로 바꿔 목록 자체를 없앴다.
#
# 필터는 두 갈래로 나눠 적용한다.
#  - placeholder 계열: 문맥이 있든 없든 시크릿이 아니다. 모든 규칙에 적용한다.
#  - structure 계열: 경로·식별자·인코딩된 미디어처럼 "높은 엔트로피지만 시크릿이
#    아닌" 모양이다. 대입 키워드 같은 문맥이 없는 후보(GENERIC)에만 적용한다.
#    `password=` 뒤에 온 값이라면 경로처럼 생겼어도 비밀번호로 보는 게 맞다.
# ---------------------------------------------------------------------------

# `your_api_key_here`, `replace-me`, `dummy-token`, `sample_secret`
# 값의 **처음이나 끝** 낱말만 본다. 값 중간에 나타나는 낱말까지 세면
# `postgresql://user:pw@db.example.com`이 `example` 하나 때문에 자리표시자로
# 걸려, 진짜 자격증명이 담긴 URL을 통째로 놓친다.
_PLACEHOLDER_WORDS = (
    r"your|my|some|change|replace|insert|put|add|todo|fixme|dummy|sample|"
    r"example|test|fake|placeholder|redacted|masked|hidden|here|none|null"
)
_PLACEHOLDER_WORD_RE = re.compile(
    rf"(?i)^(?:{_PLACEHOLDER_WORDS})(?:[_\-.]|$)"
    rf"|(?:^|[_\-.])(?:{_PLACEHOLDER_WORDS})$"
)
# 구분자 없이 붙여 쓰는 관용 자리표시자. `changeme`, `secretkey` 등.
_PLACEHOLDER_LITERAL_RE = re.compile(
    r"(?i)^(?:change(?:me|this|it)|secret(?:key)?|password|passwd|mypassword|"
    r"letmein|hunter2|foobar|notasecret|donotuse)$"
)
# `<your-token>`, `{{ vault_password }}`, `${DB_PASSWORD}`, `%SECRET%`, `$SECRET`
_TEMPLATE_RE = re.compile(r"^(?:<.*>|\{\{.*\}\}|\$\{.*\}|%[^%]*%|\$[A-Za-z_][A-Za-z0-9_]*)$")
# 마스킹·검열된 값. `****`, `xxxxxxxx`, `......`, `AAAA...`
_SINGLE_CHAR_RE = re.compile(r"^(.)\1+$")
# `abcdefgh...`, `12345678...` 같은 연속열
_SEQUENTIAL_SOURCES = ("abcdefghijklmnopqrstuvwxyz", "0123456789", "qwertyuiop")
# 짧은 단위의 반복. `1234567890abcdef`가 세 번 이어진 문서용 값 등.
_MAX_REPEATED_UNIT = 16

# 경로·URL 모양: `/`로 나뉜 조각 대부분이 소문자로 시작하는 낱말이다.
# `GATEWAY_BASE_URL/v1/chat/completions`처럼 후보 정규식이 통째로 삼킨 것들.
_PATH_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9._\-]*$")
# 인코딩된 데이터 blob은 자격증명이 아니다. 매직 프리픽스 목록으로 걸렀더니
# MP4 접두를 `AAAAIGZ0eXA`로 잘못 적어 실제 샘플(`AAAAIGZ0eXB...`)이 그대로
# 통과했다 -- 포맷마다 문자열을 더 적는 방식은 틀리기만 하고 끝이 없다.
# 길이로 가른다. 자격증명은 짧다. 이 탐지기가 아는 가장 긴 벤더 토큰이
# GitHub fine-grained PAT(93자)이고, 그보다 긴 JWT·PEM 개인키는 각자 전용
# 규칙이 잡는다. 그 위쪽은 이미지·동영상·첨부처럼 붙여넣은 데이터다.
_GENERIC_MAX_LENGTH = 512
# 콘텐츠 해시. SRI(`sha384-...`)와 다이제스트 참조(`sha256:...`).
_DIGEST_PREFIX_RE = re.compile(r"^(?:sha|md)\d*[-:]")
# 순수 16진수. 커밋 해시·UUID·다이제스트와 시크릿이 같은 알파벳을 쓰기 때문에
# 엔트로피로는 구분되지 않는다(측정: 저장소 안 hex 후보의 엔트로피 최대 3.98,
# MD5형 세션 토큰 3.64 -- 임계값을 어디에 둬도 갈라지지 않는다). 그래서 문맥
# 없는 16진수는 신호로 쓰지 않고, 대입/헤더 규칙이 문맥과 함께 잡도록 맡긴다.
_PURE_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_UUID_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$"
)


def _is_wordish(value: str) -> bool:
    """구분자로 나눈 조각이 전부 순수 알파벳인지 본다.

    자리표시자 낱말 검사를 이 경우로 한정한다. 그러지 않으면
    `api_key=sample_key_9f3a`처럼 앞머리만 낱말이고 뒤가 실제 난수인 값까지
    자리표시자로 버려 미탐이 된다. `your_api_key_here`는 전부 낱말이지만
    `sample_key_9f3a`의 `9f3a`는 아니다.
    """
    return all(segment.isalpha() for segment in re.split(r"[_\-.]", value) if segment)


def _is_single_char_run(value: str) -> bool:
    return bool(_SINGLE_CHAR_RE.match(value))


def _is_sequential(value: str) -> bool:
    lowered = value.lower()
    return any(lowered in source * 4 for source in _SEQUENTIAL_SOURCES)


def _is_repeated_unit(value: str) -> bool:
    """짧은 단위가 반복돼 만들어진 문자열인지 본다."""
    for size in range(1, min(_MAX_REPEATED_UNIT, len(value) // 2) + 1):
        if len(value) % size:
            continue
        if value == value[:size] * (len(value) // size):
            return True
    # 길이가 딱 나눠떨어지지 않아도 앞 단위가 계속 이어지면 반복으로 본다.
    for size in range(4, _MAX_REPEATED_UNIT + 1):
        unit = value[:size]
        if len(value) > size * 2 and value.startswith(unit * 2):
            return True
    return False


def is_placeholder(value: str) -> bool:
    """자리표시자·검열된 값처럼 실제 시크릿일 수 없는 모양인지 판정한다."""
    if len(value) < 2:
        return True
    return bool(
        _TEMPLATE_RE.match(value)
        or _PLACEHOLDER_LITERAL_RE.match(value)
        or (_is_wordish(value) and _PLACEHOLDER_WORD_RE.search(value))
        or _is_single_char_run(value)
        or _is_sequential(value)
        or _is_repeated_unit(value)
    )


def _is_path_like(value: str) -> bool:
    if "/" not in value:
        return False
    segments = [segment for segment in value.split("/") if segment]
    if len(segments) < 2:
        return False
    wordish = sum(1 for segment in segments if _PATH_SEGMENT_RE.match(segment))
    return wordish * 2 >= len(segments)


def is_structural(value: str) -> bool:
    """문맥 없는 고엔트로피 후보 중 시크릿이 아닌 구조물인지 판정한다."""
    return bool(
        _PURE_HEX_RE.match(value)
        or _UUID_RE.match(value)
        or _DIGEST_PREFIX_RE.match(value)
        or len(value) > _GENERIC_MAX_LENGTH
        or _is_path_like(value)
    )
