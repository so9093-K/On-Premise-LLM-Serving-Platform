"""`reset_version.py`의 공개 결과만 검증한다.

버전 변경은 하나의 원자적 release 작업이다. 파일마다 test를 늘리는 대신 stable/RC
두 결과를 한 번씩 확인해, 변경 대상과 변경 금지 대상을 함께 보호한다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESET_SCRIPT = ROOT / "scripts/build/reset_version.py"


def _load_module(tmp_root: Path):
    spec = importlib.util.spec_from_file_location("reset_version_mod", RESET_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_root
    return module


def _make_project(tmp_path: Path, version: str = "0.0.1") -> None:
    (tmp_path / "VERSION").write_text(version + "\n", encoding="utf-8")
    specs = tmp_path / "specs"
    specs.mkdir()
    for name in ("openapi.gateway.yaml", "openapi.risk-adapter.yaml"):
        (specs / name).write_text(f"info:\n  version: {version}\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(f"| 패키지 버전 | `{version}` |\n", encoding="utf-8")
    for name in (".env.example", ".env.local.example"):
        (tmp_path / name).write_text(f"PROJECT_VERSION={version}\n", encoding="utf-8")
    (tmp_path / ".env.compose.example").write_text(
        f"PROJECT_VERSION={version}\n"
        f"PLATFORM_IMAGE=ai-model-serving-platform:{version}\n"
        f"RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:{version}\n",
        encoding="utf-8",
    )
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "recommended_images.yaml").write_text(
        f"platform:\n  default: ai-model-serving-platform:{version}\n"
        f"risk_vllm:\n  default: ai-model-serving-vllm-unified:{version}\n",
        encoding="utf-8",
    )
    for name in ("model_catalog.yaml", "monitoring.yaml"):
        (configs / name).write_text("version: 0.1.0\n", encoding="utf-8")
    policy = tmp_path / "docs" / "release"
    policy.mkdir(parents=True)
    (policy / "versioning_policy.md").write_text(
        "# 버전 정책\n\n## 1. Current package version\n\n```text\n"
        f"{version}\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "version_manifest.json").write_text(
        json.dumps(
            {
                "version": version,
                "api_contract_version": version,
                "python_package_version": version,
                "image_tags": {
                    "platform": f"ai-model-serving-platform:{version}",
                    "risk_vllm": f"ai-model-serving-vllm-unified:{version}",
                },
                "config_schema_versions": {"model_catalog": "0.1.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text("0.0.1\n", encoding="utf-8")


def _run_reset(tmp_path: Path, version: str) -> None:
    module = _load_module(tmp_path)
    previous_argv = sys.argv[:]
    try:
        sys.argv = ["reset_version.py", version]
        module.main()
    finally:
        sys.argv = previous_argv


def _assert_release_files(root: Path, version: str, python_version: str) -> None:
    manifest = json.loads((root / "version_manifest.json").read_text(encoding="utf-8"))
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == version
    assert manifest["version"] == manifest["api_contract_version"] == version
    assert manifest["python_package_version"] == python_version
    assert manifest["image_tags"] == {
        "platform": f"ai-model-serving-platform:{version}",
        "risk_vllm": f"ai-model-serving-vllm-unified:{version}",
    }
    assert f'version = "{python_version}"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    for path in ("specs/openapi.gateway.yaml", "specs/openapi.risk-adapter.yaml"):
        assert f"version: {version}" in (root / path).read_text(encoding="utf-8")
    for path in (".env.example", ".env.local.example", ".env.compose.example"):
        assert f"PROJECT_VERSION={version}" in (root / path).read_text(encoding="utf-8")
    compose = (root / ".env.compose.example").read_text(encoding="utf-8")
    images = (root / "configs/recommended_images.yaml").read_text(encoding="utf-8")
    assert f"PLATFORM_IMAGE=ai-model-serving-platform:{version}" in compose
    assert f"RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:{version}" in compose
    assert f"ai-model-serving-platform:{version}" in images
    assert f"ai-model-serving-vllm-unified:{version}" in images
    assert version in (root / "README.md").read_text(encoding="utf-8")
    assert version in (root / "docs/release/versioning_policy.md").read_text(encoding="utf-8")
    assert "version: 0.1.0" in (root / "configs/model_catalog.yaml").read_text(encoding="utf-8")
    assert "version: 0.1.0" in (root / "configs/monitoring.yaml").read_text(encoding="utf-8")
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == "0.0.1\n"


def test_reset_version_updates_one_stable_release_atomically(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _run_reset(tmp_path, "0.9.0")
    _assert_release_files(tmp_path, "0.9.0", "0.9.0")


def test_reset_version_converts_release_candidate_to_pep440(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _run_reset(tmp_path, "0.9.0-rc.1")
    _assert_release_files(tmp_path, "0.9.0-rc.1", "0.9.0rc1")
