from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_bash(repo: Path, script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
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
    (repo / 'VERSION').write_text((root / 'VERSION').read_text(encoding='utf-8'), encoding='utf-8')
    (repo / 'scripts' / 'lib' / 'risk_vllm_image.sh').write_text(
        (root / 'scripts' / 'lib' / 'risk_vllm_image.sh').read_text(encoding='utf-8'),
        encoding='utf-8',
    )
    return repo


def _expected_risk_image() -> str:
    root = Path(__file__).resolve().parents[2]
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    return f'ai-model-serving-risk-vllm-kanana:{version}'


def test_risk_vllm_image_resolver_migrates_stale_env_file(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    shared = 'vllm/vllm-openai:gemma4-0505-cu129'
    (repo / '.env').write_text(
        f'VLLM_IMAGE={shared}\nRISK_VLLM_IMAGE={shared}\n',
        encoding='utf-8',
    )
    result = run_bash(
        repo,
        'source scripts/lib/risk_vllm_image.sh; '
        'risk_vllm_resolve_images .env; '
        'printf "%s\\n" "$RISK_VLLM_IMAGE_RESOLVED"',
    )
    expected = _expected_risk_image()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    text = (repo / '.env').read_text(encoding='utf-8')
    assert f'VLLM_IMAGE={shared}' in text
    assert f'RISK_VLLM_IMAGE={shared}' not in text
    assert f'RISK_VLLM_IMAGE={expected}' in text


def test_risk_vllm_image_resolver_ignores_exported_stale_base_value(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    shared = 'vllm/vllm-openai:gemma4-0505-cu129'
    (repo / '.env').write_text(f'VLLM_IMAGE={shared}\n', encoding='utf-8')
    result = run_bash(
        repo,
        'source scripts/lib/risk_vllm_image.sh; '
        'risk_vllm_resolve_images .env; '
        'printf "%s\\n" "$RISK_VLLM_IMAGE_RESOLVED"',
        env={'RISK_VLLM_IMAGE': shared},
    )
    expected = _expected_risk_image()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert f'RISK_VLLM_IMAGE={expected}' in (repo / '.env').read_text(encoding='utf-8')


def test_risk_vllm_image_resolver_preserves_custom_exported_image(tmp_path):
    repo = copy_minimal_repo(tmp_path)
    shared = 'vllm/vllm-openai:gemma4-0505-cu129'
    custom = 'registry.example.com/custom/kanana-risk:dev'
    (repo / '.env').write_text(f'VLLM_IMAGE={shared}\n', encoding='utf-8')
    result = run_bash(
        repo,
        'source scripts/lib/risk_vllm_image.sh; '
        'risk_vllm_resolve_images .env; '
        'printf "%s\\n" "$RISK_VLLM_IMAGE_RESOLVED"',
        env={'RISK_VLLM_IMAGE': custom},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == custom
