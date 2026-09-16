from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/deploy/runtime-bin/python3.12"


def _fake_docker(bin_dir: Path) -> Path:
    script = bin_dir / "docker"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        ": \"${DOCKER_ARGS_FILE:?}\"\n"
        "printf '%s\\n' \"$@\" > \"${DOCKER_ARGS_FILE}\"\n"
        "cat\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _runner_env(
    tmp_path: Path,
    deploy_root: Path,
    *,
    candidate_image: str,
    platform_image: str | None = None,
) -> tuple[dict[str, str], Path]:
    args_file = tmp_path / "docker-args.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_docker(fake_bin)
    env = os.environ.copy()
    env.update(
        {
            "DEPLOY_PATH": str(deploy_root),
            "PLATFORM_IMAGE_TO_DEPLOY": candidate_image,
            "DOCKER_ARGS_FILE": str(args_file),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    env.pop("PLATFORM_IMAGE", None)
    if platform_image is not None:
        env["PLATFORM_IMAGE"] = platform_image
    return env, args_file


def test_remote_python_runner_uses_platform_image_and_release_source(tmp_path: Path) -> None:
    deploy_root = tmp_path / "deploy"
    release_root = deploy_root / "releases" / "r1"
    release_root.mkdir(parents=True)
    candidate_image = "registry.example/platform@sha256:" + "a" * 64
    env, args_file = _runner_env(
        tmp_path,
        deploy_root,
        candidate_image=candidate_image,
    )
    env["DEPLOY_TEST_SECRET"] = "not-written-into-argv"

    result = subprocess.run(
        [str(RUNNER), "-c", "print('ok')"],
        cwd=release_root,
        env=env,
        input="stdin-payload\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "stdin-payload\n"
    args = args_file.read_text(encoding="utf-8").splitlines()
    resolved_deploy = str(deploy_root.resolve())
    resolved_release = str(release_root.resolve())
    assert ["--network", "host"] == args[args.index("--network") : args.index("--network") + 2]
    assert ["--workdir", resolved_release] == args[args.index("--workdir") : args.index("--workdir") + 2]
    assert f"{resolved_deploy}:{resolved_deploy}" in args
    assert "DEPLOY_TEST_SECRET" in args
    assert "not-written-into-argv" not in args
    assert f"PYTHONPATH={resolved_release}/src:{resolved_release}" in args
    assert f"APP_CONFIG_ROOT={resolved_release}" in args
    entrypoint = args.index("--entrypoint")
    assert args[entrypoint + 1] == "/app/.venv/bin/python"
    assert args[entrypoint + 2] == candidate_image
    assert args[-2:] == ["-c", "print('ok')"]


def test_remote_python_runner_tracks_restored_platform_image(tmp_path: Path) -> None:
    deploy_root = tmp_path / "deploy"
    previous_release = deploy_root / "releases" / "previous"
    previous_release.mkdir(parents=True)
    candidate_image = "registry.example/platform@sha256:" + "a" * 64
    restored_image = "registry.example/platform@sha256:" + "b" * 64
    env, args_file = _runner_env(
        tmp_path,
        deploy_root,
        candidate_image=candidate_image,
        platform_image=restored_image,
    )

    result = subprocess.run(
        [str(RUNNER), "-c", "print('rollback')"],
        cwd=previous_release,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    args = args_file.read_text(encoding="utf-8").splitlines()
    entrypoint = args.index("--entrypoint")
    assert args[entrypoint + 2] == restored_image
    assert candidate_image not in args[entrypoint + 2 :]


def test_remote_python_runner_rejects_working_directory_outside_deploy_root(tmp_path: Path) -> None:
    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    candidate_image = "registry.example/platform@sha256:" + "c" * 64
    env, _ = _runner_env(
        tmp_path,
        deploy_root,
        candidate_image=candidate_image,
    )

    result = subprocess.run(
        [str(RUNNER), "-c", "print('should-not-run')"],
        cwd=outside,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert "refusing to run outside deployment root" in result.stderr
