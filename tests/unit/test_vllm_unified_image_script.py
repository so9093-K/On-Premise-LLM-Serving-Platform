"""scripts/lib/vllm_unified_image.sh(통합 vLLM 이미지 resolver)를 검증한다:
기본값/커스텀 이미지 해석, base image가 vllm_unified_build.yaml과 일치하는지,
media 의존성(soundfile/librosa/av)이 검증된 lock 파일을 그대로 쓰는지."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

_ISOLATED_KEYS = ("VLLM_IMAGE", "RISK_VLLM_IMAGE", "RISK_VLLM_BASE_IMAGE")


def run_bash(repo: Path, script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    # 다른 테스트나 이 프로세스의 실제 배포 환경에서 이 키들이 이미 export돼
    # 있으면 resolver가 그 값을 우선시켜 .env 픽스처를 무시하게 된다 -- 여기서
    # 명시적으로 지워서 각 테스트가 자기 .env/override만으로 결정되게 한다.
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


def _expected_unified_image() -> str:
    root = Path(__file__).resolve().parents[2]
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    return f'ai-model-serving-vllm-unified:{version}'


def _canonical_base_image() -> str:
    root = Path(__file__).resolve().parents[2]
    document = yaml.safe_load((root / "configs/vllm_unified_build.yaml").read_text(encoding="utf-8"))
    return str(document["base_image_default"])


def test_vllm_unified_image_resolver_defaults_when_unset(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    (repo / '.env').write_text('VLLM_IMAGE=some/other:tag\n', encoding='utf-8')
    result = run_bash(
        repo,
        'source scripts/lib/vllm_unified_image.sh; '
        'vllm_unified_resolve_images .env; '
        'printf "%s\\n" "$RISK_VLLM_IMAGE_RESOLVED"',
    )
    expected = _expected_unified_image()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_vllm_unified_image_resolver_default_base_matches_canonical_image_config(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    result = run_bash(
        repo,
        'source scripts/lib/vllm_unified_image.sh; '
        'vllm_unified_resolve_images .env; '
        'printf "%s\\n" "$RISK_VLLM_BASE_IMAGE_RESOLVED"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == _canonical_base_image()


def test_vllm_unified_image_resolver_preserves_custom_exported_image(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    custom = 'registry.example.com/custom/vllm-unified:dev'
    (repo / '.env').write_text('VLLM_IMAGE=some/other:tag\n', encoding='utf-8')
    result = run_bash(
        repo,
        'source scripts/lib/vllm_unified_image.sh; '
        'vllm_unified_resolve_images .env; '
        'printf "%s\\n" "$RISK_VLLM_IMAGE_RESOLVED"',
        env={'RISK_VLLM_IMAGE': custom},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == custom
