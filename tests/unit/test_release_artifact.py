from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.release.release_artifact import (
    MANIFEST_NAME,
    PROVENANCE_NAME,
    ReleaseArtifactError,
    build_manifest,
    materialize,
    verify_materialized,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _source_repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release-test@example.invalid")

    _write(
        root / "configs/env_contract.yaml",
        "env_examples:\n  .env.safe.example: safe release example\n",
    )
    _write(root / "src/app.py", "VALUE = 1\n")
    _write(root / "scripts/run.sh", "#!/usr/bin/env bash\necho ok\n")
    (root / "scripts/run.sh").chmod(0o755)
    _write(root / "tests/test_smoke.py", "def test_smoke():\n    assert True\n")
    _write(root / ".env.safe.example", "SAFE=value\n")

    # These are deliberately tracked so the release resolver, not .gitignore, proves
    # they are excluded from the immutable payload.
    _write(root / ".github/workflows/ci.yml", "name: ci\n")
    _write(root / ".env", "SECRET=do-not-package\n")
    _write(root / ".runtime/state.json", "{}\n")
    _write(root / "reports/runtime/local.json", "{}\n")

    _git(root, "add", "-f", ".")
    _git(root, "commit", "-m", "test release source")
    return root


def test_manifest_is_the_canonical_payload_contract(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    manifest = build_manifest(source, version="1.2.3")
    paths = {entry["path"] for entry in manifest["files"]}

    assert manifest["schema_version"] == 1
    assert manifest["source"]["tracked_state"] == "clean"
    assert "src/app.py" in paths
    assert "scripts/run.sh" in paths
    assert "tests/test_smoke.py" in paths
    assert ".env.safe.example" in paths
    assert ".github/workflows/ci.yml" not in paths
    assert ".env" not in paths
    assert ".runtime/state.json" not in paths
    assert "reports/runtime/local.json" not in paths

    release = tmp_path / "release"
    materialize(source, release, manifest)
    verified = verify_materialized(release)
    assert verified["payload_sha256"] == manifest["payload_sha256"]
    assert stat.S_IMODE((release / "src/app.py").stat().st_mode) == 0o644
    assert stat.S_IMODE((release / "scripts/run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((release / MANIFEST_NAME).stat().st_mode) == 0o644

    provenance = json.loads((release / PROVENANCE_NAME).read_text(encoding="utf-8"))
    assert provenance["derived_from"] == MANIFEST_NAME
    assert provenance["payload_sha256"] == manifest["payload_sha256"]
    assert provenance["source_revision"] == manifest["source"]["revision"]


def test_untracked_files_do_not_change_tracked_release_identity(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    before = build_manifest(source, version="1.2.3")
    _write(source / "notes.local.txt", "not part of the release\n")
    after = build_manifest(source, version="1.2.3")

    assert before["source"]["tracked_state"] == "clean"
    assert after["source"]["tracked_state"] == "clean"
    assert after["files"] == before["files"]
    assert after["payload_sha256"] == before["payload_sha256"]


def test_dirty_tracked_source_is_explicit_and_manifest_freezes_bytes(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    _write(source / "src/app.py", "VALUE = 2\n")
    manifest = build_manifest(source, version="1.2.3")
    assert manifest["source"]["tracked_state"] == "dirty"

    # Local packaging may intentionally describe a dirty tracked tree, but once the
    # manifest exists its bytes are immutable. A later edit must fail closed.
    _write(source / "src/app.py", "VALUE = 3\n")
    with pytest.raises(ReleaseArtifactError, match="changed after manifest resolution"):
        materialize(source, tmp_path / "release", manifest)


def test_remote_style_verification_detects_payload_tampering(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    manifest = build_manifest(source, version="1.2.3")
    release = tmp_path / "release"
    materialize(source, release, manifest)

    _write(release / "src/app.py", "VALUE = 999\n")
    with pytest.raises(ReleaseArtifactError, match="payload mismatch"):
        verify_materialized(release)


def test_package_and_remote_deploy_share_one_materializer() -> None:
    root = Path(__file__).resolve().parents[2]
    package_script = (root / "scripts/build/package_release.sh").read_text(encoding="utf-8")
    deploy_script = (root / "scripts/deploy/deploy_compose_release.sh").read_text(
        encoding="utf-8"
    )

    assert "scripts/release/release_artifact.py materialize" in package_script
    assert "scripts/release/release_artifact.py materialize" in deploy_script
    assert "release_artifact.py\" verify" in deploy_script
    assert "git ls-files" not in package_script
    assert "git ls-files" not in deploy_script
    assert "--exclude \".github" not in deploy_script
