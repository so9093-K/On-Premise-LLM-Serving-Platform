"""실제로 package_release.sh를 실행해 만든 릴리스 ZIP의 위생을 검증하는 smoke 테스트.

캐시/시크릿/개발 도구(.git, .claude 등) 미포함, 타임스탬프 정규화, 운영 리포트
제외 등 "ZIP에 뭐가 들어있으면 안 되는지"를 실제 산출물을 열어서 확인한다.
"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = "ai_model_serving_platform"


@pytest.fixture(scope="module")
def package_zip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dist = tmp_path_factory.mktemp("release-package-dist")
    env = os.environ.copy()
    env["PACKAGE_DIST"] = str(dist)
    env["PACKAGE_SKIP_VALIDATION"] = "1"
    result = subprocess.run(
        ["bash", "scripts/build/package_release.sh"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    package = Path(result.stdout.strip().splitlines()[-1])
    assert package.parent == dist
    assert package.exists()
    assert not list(dist.glob("*.tmp.*")), "package temp ZIP must not remain after success"
    return package


@pytest.mark.slow
def test_release_package_zip_hygiene_is_isolated(package_zip: Path) -> None:
    with zipfile.ZipFile(package_zip) as zf:
        names = zf.namelist()
        infos = zf.infolist()

    forbidden_fragments = [
        ".pytest_cache",
        "__pycache__",
        ".pyc",
        "/dist/",
        ".egg-info",
        "/.git/",
        "/.runtime/",
        "/.other/",
        "/.agents/",
        "/.codex/",
        "/.claude/",
        "/.cursor/",
        "/run/",
        "/model_cache/",
    ]
    assert names
    assert all(name == f"{PACKAGE_ROOT}/" or name.startswith(f"{PACKAGE_ROOT}/") for name in names)
    assert not any(any(fragment in name for fragment in forbidden_fragments) for name in names)
    for safe_env in [".env.example", ".env.local.example", ".env.compose.example"]:
        assert f"{PACKAGE_ROOT}/{safe_env}" in names
    assert f"{PACKAGE_ROOT}/scripts/env/env_get.py" in names
    assert f"{PACKAGE_ROOT}/scripts/env/env_validate.py" in names

    epoch = (1980, 1, 1, 0, 0, 0)
    non_normalized = [info.filename for info in infos if info.date_time != epoch]
    assert not non_normalized, f"non-normalized timestamps in zip: {non_normalized[:5]}"


@pytest.mark.slow
def test_release_package_excludes_runtime_reports_and_keeps_model_docs(package_zip: Path) -> None:
    with zipfile.ZipFile(package_zip) as zf:
        names = set(zf.namelist())

    assert not any(name.startswith(f"{PACKAGE_ROOT}/reports/runtime/") for name in names)

    assert f"{PACKAGE_ROOT}/docs/models/model_cards.md" in names
    assert f"{PACKAGE_ROOT}/docs/archive/reviews/gemma4_31b_awq_review.md" in names
    assert f"{PACKAGE_ROOT}/docs/models/model_runtime_source_review.md" in names
    assert not any(name.startswith(f"{PACKAGE_ROOT}/models/") for name in names)
