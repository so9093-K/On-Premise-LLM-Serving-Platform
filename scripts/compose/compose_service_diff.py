#!/usr/bin/env python3
"""두 release의 compose 파일을 서비스 단위로 비교해, 정의가 실제로 바뀐 서비스만 출력한다.

배포는 compose 파일이 바뀌면 어느 서비스 정의가 바뀌었는지 알 수 없어 전부
재생성해 왔다. 그 대가가 크다 -- 실측으로 gateway의 environment 세 줄만 바뀐
배포가 서비스 13개를 전부 재생성했고, 직렬로 뜨는 GPU 모델 세 개를 다시 태워
4분 40초를 썼다.

렌더된 정의를 비교하면 이 판단을 서비스 단위로 좁힐 수 있다. YAML anchor와
`${VAR}` 치환이 이미 펼쳐진 뒤라, 파일 어디가 바뀌었든 그 영향을 실제로 받는
서비스에서만 차이가 드러난다.

다만 release 디렉터리 경로는 지워야 한다. compose의 상대 bind mount는 compose
파일 위치를 기준으로 풀리므로 release마다 절대 경로가 달라지고, 내용이 같은
서비스도 매번 바뀐 것처럼 보인다. 실제로 그 허상 때문에 13개 중 7개가 바뀐
것으로 잡혔다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PLACEHOLDER = "@RELEASE@"


def _load(path: Path, strip: str) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if strip:
        text = text.replace(strip, _PLACEHOLDER)
    payload = json.loads(text)
    services = payload.get("services")
    if not isinstance(services, dict):
        raise ValueError(f"{path}: rendered compose has no services mapping")
    return services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List services whose rendered compose definition changed."
    )
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument(
        "--strip-before",
        default="",
        help="baseline release 디렉터리. 렌더 결과에서 이 경로를 지운다.",
    )
    parser.add_argument("--strip-after", default="")
    args = parser.parse_args(argv)

    before = _load(args.before, args.strip_before)
    after = _load(args.after, args.strip_after)

    # 사라진 서비스는 출력하지 않는다. 재생성할 대상이 아니라 제거 대상이고,
    # 그건 compose의 --remove-orphans가 소유한다.
    for name in sorted(after):
        if before.get(name) != after[name]:
            print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
