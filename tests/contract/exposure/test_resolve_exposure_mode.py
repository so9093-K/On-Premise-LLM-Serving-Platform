"""compose 기동 경로가 쓰는 exposure mode 해석기를 검증한다.

resolve()는 compose_up.sh/compose_config.sh/bootstrap.sh가 override 파일을 고르기
전에 부르는 게이트다. 알 수 없는 mode에서 조용히 기본값으로 흐르면 의도하지 않은
노출 프로필로 기동되므로, exit code 2로 멈추는 것이 계약이다.
"""

from __future__ import annotations

import pytest

from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve

from .helpers import ROOT


def test_resolve_exposure_mode_fails_on_unknown_mode() -> None:
    data = load_exposure_data(ROOT)
    with pytest.raises(SystemExit) as exc_info:
        resolve("unsupported_mode", data)
    assert exc_info.value.code == 2
