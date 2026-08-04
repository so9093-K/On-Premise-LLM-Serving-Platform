"""reset_version.py 동작 검증.

make reset-version NEW_VERSION=x.y.z 후 package version, API contract version,
platform/risk_vllm image tag, env example, recommended image 대상이 새 버전으로 갱신되고,
변경 제외 대상(config schema version, CHANGELOG, historical reports)은 변경되지 않아야 한다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESET_SCRIPT = ROOT / "scripts/build/reset_version.py"

OLD_VERSION = "0.0.1"
NEW_VERSION = "0.9.0"
NEW_PY_VERSION = "0.9.0"
NEW_RC_VERSION = "0.9.0-rc.1"
NEW_RC_PY_VERSION = "0.9.0rc1"


def _load_module(tmp_root: Path):
    spec = importlib.util.spec_from_file_location("reset_version_mod", RESET_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = tmp_root
    return mod


def _make_project(tmp_path: Path, version: str = OLD_VERSION) -> None:
    """reset_version.py가 수정하는 것과 맞춘 최소 파일 트리."""
    (tmp_path / "VERSION").write_text(version + "\n", encoding="utf-8")

    specs = tmp_path / "specs"
    specs.mkdir()
    for name in ("openapi.gateway.yaml", "openapi.risk-adapter.yaml"):
        (specs / name).write_text(
            f"info:\n  version: {version}\n  title: test\n", encoding="utf-8"
        )

    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "ai-model-serving"\nversion = "{version}"\n', encoding="utf-8"
    )

    (tmp_path / "README.md").write_text(
        f"| 패키지 버전 | `{version}` |\n", encoding="utf-8"
    )

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
    for cfg in ("model_catalog.yaml", "monitoring.yaml"):
        (configs / cfg).write_text("version: 0.1.0\nschema: placeholder\n", encoding="utf-8")

    docs_release = tmp_path / "docs" / "release"
    docs_release.mkdir(parents=True)
    (docs_release / "versioning_policy.md").write_text(
        f"# 버전 정책\n\n## 1. Current package version\n\n```text\n{version}\n```\n",
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
                "release_stage": "release",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (tmp_path / "CHANGELOG.md").write_text(
        f"## [{version}] - 2026-01-01\n\n- Initial release.\n", encoding="utf-8"
    )


def _run_reset(tmp_path: Path, version: str) -> None:
    mod = _load_module(tmp_path)
    old_argv = sys.argv[:]
    try:
        sys.argv = ["reset_version.py", version]
        mod.main()
    finally:
        sys.argv = old_argv


class TestResetVersionStable:
    """안정 버전 x.y.z reset."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        _make_project(tmp_path, OLD_VERSION)
        _run_reset(tmp_path, NEW_VERSION)
        self.root = tmp_path

    def test_version_file(self):
        assert (self.root / "VERSION").read_text(encoding="utf-8").strip() == NEW_VERSION

    def test_manifest_version(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["version"] == NEW_VERSION

    def test_manifest_python_package_version(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["python_package_version"] == NEW_PY_VERSION

    def test_manifest_api_contract_version(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["api_contract_version"] == NEW_VERSION

    def test_manifest_image_tags_platform(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["image_tags"]["platform"] == f"ai-model-serving-platform:{NEW_VERSION}"

    def test_manifest_config_schema_versions_unchanged(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["config_schema_versions"]["model_catalog"] == "0.1.0"

    def test_pyproject_toml(self):
        text = (self.root / "pyproject.toml").read_text(encoding="utf-8")
        assert f'version = "{NEW_PY_VERSION}"' in text

    def test_openapi_gateway(self):
        text = (self.root / "specs/openapi.gateway.yaml").read_text(encoding="utf-8")
        assert f"  version: {NEW_VERSION}" in text

    def test_openapi_risk_adapter(self):
        text = (self.root / "specs/openapi.risk-adapter.yaml").read_text(encoding="utf-8")
        assert f"  version: {NEW_VERSION}" in text

    def test_readme(self):
        text = (self.root / "README.md").read_text(encoding="utf-8")
        assert f"`{NEW_VERSION}`" in text

    def test_env_example_project_version(self):
        text = (self.root / ".env.example").read_text(encoding="utf-8")
        assert f"PROJECT_VERSION={NEW_VERSION}" in text

    def test_env_compose_project_version(self):
        text = (self.root / ".env.compose.example").read_text(encoding="utf-8")
        assert f"PROJECT_VERSION={NEW_VERSION}" in text

    def test_env_compose_platform_image(self):
        text = (self.root / ".env.compose.example").read_text(encoding="utf-8")
        assert f"PLATFORM_IMAGE=ai-model-serving-platform:{NEW_VERSION}" in text

    def test_recommended_images_platform(self):
        text = (self.root / "configs/recommended_images.yaml").read_text(encoding="utf-8")
        assert f"ai-model-serving-platform:{NEW_VERSION}" in text

    def test_recommended_images_risk_vllm_updated(self):
        text = (self.root / "configs/recommended_images.yaml").read_text(encoding="utf-8")
        assert f"ai-model-serving-vllm-unified:{NEW_VERSION}" in text

    def test_manifest_risk_vllm_updated(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["image_tags"]["risk_vllm"] == f"ai-model-serving-vllm-unified:{NEW_VERSION}"

    def test_env_compose_risk_vllm_image(self):
        text = (self.root / ".env.compose.example").read_text(encoding="utf-8")
        assert f"RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:{NEW_VERSION}" in text

    def test_versioning_policy_current_block(self):
        text = (self.root / "docs/release/versioning_policy.md").read_text(encoding="utf-8")
        assert NEW_VERSION in text

    def test_config_schema_versions_unchanged(self):
        for cfg in ("model_catalog.yaml", "monitoring.yaml"):
            text = (self.root / "configs" / cfg).read_text(encoding="utf-8")
            assert "version: 0.1.0" in text

    def test_changelog_unchanged(self):
        text = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        assert OLD_VERSION in text


class TestResetVersionRC:
    """Release candidate x.y.z-rc.n reset 검증."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        _make_project(tmp_path, OLD_VERSION)
        _run_reset(tmp_path, NEW_RC_VERSION)
        self.root = tmp_path

    def test_version_file(self):
        assert (self.root / "VERSION").read_text(encoding="utf-8").strip() == NEW_RC_VERSION

    def test_manifest_python_package_version_pep440(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["python_package_version"] == NEW_RC_PY_VERSION

    def test_pyproject_toml_pep440(self):
        text = (self.root / "pyproject.toml").read_text(encoding="utf-8")
        assert f'version = "{NEW_RC_PY_VERSION}"' in text

    def test_manifest_image_tags_platform_rc(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["image_tags"]["platform"] == f"ai-model-serving-platform:{NEW_RC_VERSION}"

    def test_manifest_image_tags_risk_vllm_rc(self):
        m = json.loads((self.root / "version_manifest.json").read_text(encoding="utf-8"))
        assert m["image_tags"]["risk_vllm"] == f"ai-model-serving-vllm-unified:{NEW_RC_VERSION}"

    def test_env_compose_risk_vllm_image_rc(self):
        text = (self.root / ".env.compose.example").read_text(encoding="utf-8")
        assert f"RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:{NEW_RC_VERSION}" in text
