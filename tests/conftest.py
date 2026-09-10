"""테스트 수집 전에 필요한 최소 환경을 한 곳에서 설정한다.

``make test``뿐 아니라 IDE와 직접 ``python -m pytest`` 실행도 같은 테스트
환경을 사용해야 한다. 실제 배포 이미지를 가리키지 않는 digest 형식의 값만 두며,
개별 테스트가 명시한 환경은 덮어쓰지 않는다.
"""

from __future__ import annotations

import os


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "VLLM_IMAGE",
    "registry.example.com/vllm-unified@sha256:"
    "0000000000000000000000000000000000000000000000000000000000000000",
)
os.environ.setdefault(
    "AUDIO_VLLM_IMAGE",
    "registry.example.com/vllm-unified@sha256:"
    "1111111111111111111111111111111111111111111111111111111111111111",
)
