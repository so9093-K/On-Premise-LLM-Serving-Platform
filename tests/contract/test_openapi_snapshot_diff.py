from __future__ import annotations

from scripts.validation.openapi_snapshot_diff import main


def test_openapi_snapshot_diff_passes_for_strict_auth_surface() -> None:
    assert main() == 0
