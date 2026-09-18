from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYSTEM_COMPONENTS = ROOT / "docs/03_system_components.md"
TERMINOLOGY = ROOT / "docs/reference/terminology.md"


def test_runtime_controller_is_the_user_facing_component_name() -> None:
    text = SYSTEM_COMPONENTS.read_text(encoding="utf-8")

    assert "Runtime Controller" in text
    assert "Admin / Control Sidecar" not in text
    assert "Admin Sidecar" not in text
    assert "Sidecar" not in text


def test_runtime_controller_legacy_identifier_is_explicitly_classified() -> None:
    text = TERMINOLOGY.read_text(encoding="utf-8")

    assert "| **Runtime Controller** |" in text
    assert "| **Admin Sidecar** |" in text
    assert "`admin-sidecar` / `admin_sidecar` | Runtime Controller" in text
