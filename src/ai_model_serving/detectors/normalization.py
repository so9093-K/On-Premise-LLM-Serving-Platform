from __future__ import annotations

import unicodedata

# 서식 제어문자(Unicode general category Cf) -- zero-width space/joiner, soft
# hyphen, BOM 등. NFKC는 이들을 지우지 않으므로 따로 걷어내야 한다.
# `９０１２０１－…`(전각)은 NFKC가 처리하지만 `901201-1<ZWSP>234560`은 처리하지
# 못해, 정규식이 숫자 사이에서 끊겨 통째로 미탐이 됐다.
_FORMAT_CATEGORY = "Cf"

OriginMap = tuple[int, ...]


def normalize_with_origin(text: str) -> tuple[str, OriginMap | None]:
    """NFKC 폴딩과 Cf 제거를 적용한 텍스트와 인덱스 역매핑을 반환한다.

    두 번째 값은 정규화 텍스트의 인덱스 i가 원문의 어느 인덱스에서 왔는지를
    담는다. 원문이 그대로면 None을 돌려주고, 호출자는 변환을 건너뛴다.

    문자열 전체가 아니라 **문자 단위로** NFKC를 적용한다. 전체 정규화는 길이가
    변하는 지점을 알려주지 않아 원문 offset으로 되돌아갈 수 없기 때문이다.
    문자 단위 NFKC는 전체 정규화와 달리 문자 경계를 넘는 결합(예: 낱자 자모의
    음절 합성)을 하지 않지만, 이 탐지기가 상대하는 전각 숫자/영문/하이픈은
    모두 1:1로 접혀 결과가 같다. 또 유니코드 전 영역에서 문자 단위 NFKC가
    길이 0으로 사라지는 코드포인트는 없어서(확장만 존재) 역매핑이 항상 성립한다.
    """
    # ASCII는 NFKC 고정점이고 Cf도 없다. 순수 ASCII 프롬프트에서 맵 생성 비용을
    # 통째로 건너뛰기 위한 빠른 경로다.
    if text.isascii():
        return text, None

    chunks: list[str] = []
    origin: list[int] = []
    changed = False
    for index, char in enumerate(text):
        if unicodedata.category(char) == _FORMAT_CATEGORY:
            changed = True
            continue
        folded = unicodedata.normalize("NFKC", char)
        if folded != char:
            changed = True
        chunks.append(folded)
        origin.extend([index] * len(folded))

    if not changed:
        # 한글 음절처럼 NFKC 고정점인 비ASCII 텍스트가 여기로 온다.
        return text, None
    return "".join(chunks), tuple(origin)


def to_origin_span(origin: OriginMap | None, start: int, end: int) -> tuple[int, int]:
    """정규화 텍스트 기준 span을 원문 기준 span으로 되돌린다.

    끝 인덱스는 span의 마지막 문자가 유래한 원문 위치 다음 칸이다. 원문에서
    span 사이에 끼어 있던 Cf 문자는 이 범위 안에 자연히 포함되므로, 마스킹이
    회피용으로 삽입된 문자까지 함께 치환한다.
    """
    if origin is None:
        return start, end
    if not origin or start >= len(origin) or end <= start:
        return start, end
    return origin[start], origin[min(end, len(origin)) - 1] + 1
