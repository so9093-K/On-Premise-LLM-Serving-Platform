"""Compose 예시의 local_open exposure 기본 정책을 검증한다."""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403


def test_compose_example_local_open_uses_full_stack_private_lan_policy() -> None:
    text = (ROOT / ".env.compose.example").read_text(encoding="utf-8")
    assert "AUTH_MODE=local_open" in text
    assert "EXPOSURE_MODE=master_open" in text
    assert "EXPOSURE_AUDIENCE=private_lan" in text
