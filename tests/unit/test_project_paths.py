from __future__ import annotations

from pathlib import Path

import pytest

from ai_model_serving.project_paths import resolve_project_root


def test_resolve_project_root_uses_caller_required_markers(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "specs" / "schemas").mkdir(parents=True)
    (root / "VERSION").write_text("test\n", encoding="utf-8")

    assert resolve_project_root(
        root,
        required_paths=("VERSION", "specs/schemas"),
        strict=True,
    ) == root.resolve()


def test_resolve_project_root_uses_app_config_root_for_installed_package_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "image-app"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "auth_profiles.yaml").write_text("profiles: {}\n", encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG_ROOT", str(root))

    assert resolve_project_root(
        required_paths=("configs/auth_profiles.yaml",), strict=True
    ) == root.resolve()


def test_resolve_project_root_strict_mode_rejects_missing_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="could not locate project root"):
        resolve_project_root(
            tmp_path,
            required_paths=("VERSION", "definitely-not-a-project-marker"),
            strict=True,
        )
