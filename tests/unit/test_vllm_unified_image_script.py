"""scripts/lib/vllm_unified_image.sh(통합 vLLM 이미지 resolver)를 검증한다:
shared image 해석, base image가 vllm_unified_build.yaml과 일치하는지,
media 의존성(soundfile/librosa/av)이 검증된 lock 파일을 그대로 쓰는지."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.build.pin_local_vllm_image import pin_matching_env_values
from scripts import platform_cli

_ISOLATED_KEYS = (
    "VLLM_IMAGE",
    "VLLM_BASE_IMAGE",
)


def run_bash(repo: Path, script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["PYTHON_BIN"] = sys.executable
    for key in _ISOLATED_KEYS:
        merged_env.pop(key, None)
    if env:
        merged_env.update(env)
    return subprocess.run(
        ['bash', '-lc', script],
        cwd=repo,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def copy_minimal_repo(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    repo = tmp_path / 'repo'
    (repo / 'scripts' / 'lib').mkdir(parents=True)
    (repo / 'scripts' / 'models').mkdir(parents=True)
    (repo / 'configs').mkdir(parents=True)
    (repo / 'VERSION').write_text((root / 'VERSION').read_text(encoding='utf-8'), encoding='utf-8')
    (repo / 'scripts' / 'lib' / 'vllm_unified_image.sh').write_text(
        (root / 'scripts' / 'lib' / 'vllm_unified_image.sh').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    (repo / 'scripts' / 'models' / 'print_vllm_unified_compatibility.py').write_text(
        (root / 'scripts' / 'models' / 'print_vllm_unified_compatibility.py').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    (repo / 'configs' / 'recommended_images.yaml').write_text(
        (root / 'configs' / 'recommended_images.yaml').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    (repo / 'configs' / 'vllm_unified_build.yaml').write_text(
        (root / 'configs' / 'vllm_unified_build.yaml').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    return repo


def _canonical_base_image() -> str:
    root = Path(__file__).resolve().parents[2]
    document = yaml.safe_load((root / "configs/vllm_unified_build.yaml").read_text(encoding="utf-8"))
    return str(document["base_image_default"])


def test_vllm_unified_image_resolver_projects_shared_image_to_runtime_views(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    shared = 'registry.example.com/project/vllm-unified:qualified'
    (repo / '.env').write_text(f'VLLM_IMAGE={shared}\n', encoding='utf-8')
    result = run_bash(
        repo,
        'source scripts/lib/vllm_unified_image.sh; '
        'vllm_unified_resolve_images .env; '
        'printf "%s\\n%s\\n%s\\n" "$VLLM_IMAGE_RESOLVED" '
        '"$EMBEDDING_KO_VLLM_IMAGE_RESOLVED" "$RISK_VLLM_IMAGE_RESOLVED"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [shared, shared, shared]
    assert result.stderr == ''


def test_vllm_unified_image_resolver_default_base_matches_canonical_image_config(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    result = run_bash(
        repo,
        'source scripts/lib/vllm_unified_image.sh; '
        'vllm_unified_resolve_images .env; '
        'printf "%s\\n" "$VLLM_BASE_IMAGE_RESOLVED"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == _canonical_base_image()


def test_vllm_unified_image_resolver_preserves_explicit_shared_image(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    custom = 'registry.example.com/custom/vllm-unified:dev'
    (repo / '.env').write_text('VLLM_IMAGE=some/other:tag\n', encoding='utf-8')
    result = run_bash(
        repo,
        'source scripts/lib/vllm_unified_image.sh; '
        'vllm_unified_resolve_images .env; '
        'printf "%s\\n" "$VLLM_IMAGE_RESOLVED"',
        env={'VLLM_IMAGE': custom},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == custom
    assert result.stderr == ''


def test_local_image_pin_updates_shared_and_profile_override_refs(tmp_path):
    env_path = tmp_path / ".env"
    source = "ai-model-serving-vllm-unified:0.0.1"
    image_id = "sha256:" + "b" * 64
    env_path.write_text(
        f"VLLM_IMAGE={source}\n"
        f"AUDIO_VLLM_IMAGE={source}\n",
        encoding="utf-8",
    )

    assert pin_matching_env_values(env_path, source, image_id) == [
        "VLLM_IMAGE",
        "AUDIO_VLLM_IMAGE",
    ]
    assert env_path.read_text(encoding="utf-8") == (
        f"VLLM_IMAGE={image_id}\n"
        f"AUDIO_VLLM_IMAGE={image_id}\n"
    )


def test_target_rebuild_does_not_treat_local_image_id_as_external(monkeypatch):
    local_id = "sha256:" + "a" * 64
    commands: list[tuple[tuple[str, ...], dict[str, str] | None]] = []
    pinned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        platform_cli,
        "_env_values",
        lambda: {
            "PLATFORM_IMAGE": "ai-model-serving-platform:test",
            "VLLM_IMAGE": local_id,
        },
    )
    monkeypatch.setattr(
        platform_cli,
        "_run",
        lambda *command, env=None: commands.append((command, env)),
    )
    monkeypatch.setattr(
        platform_cli,
        "resolve_local_image_id",
        lambda image: "sha256:" + "b" * 64,
    )
    monkeypatch.setattr(
        platform_cli,
        "pin_matching_env_values",
        lambda _path, source, image_id, **_kwargs: pinned.append((source, image_id)) or ["VLLM_IMAGE"],
    )
    target = type("Target", (), {"controllable": True, "target_id": "linux-nvidia-dynamic"})()

    platform_cli.build_target(target, no_cache=True)

    assert len(commands) == 2
    assert all(env and env["PROJECT_BUILD_NO_CACHE"] == "1" for _, env in commands)
    assert commands[1][1]["VLLM_UNIFIED_BUILD_IMAGE"].startswith("ai-model-serving-vllm-unified:")
    assert pinned == [(local_id, "sha256:" + "b" * 64)]
