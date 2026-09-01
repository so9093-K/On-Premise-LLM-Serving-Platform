"""scripts/lib/deploy_recreate_policy.sh를 검증한다: 컨테이너 재생성이 필요한
조건(불변 digest로 새 이미지가 준비됐는지, vllm_unified_build.yaml의 빌드
입력이 실제로 바뀌었는지)을 셸 함수 단위로 확인한다."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _policy_accepts(image_ref: str) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source scripts/lib/deploy_recreate_policy.sh; deploy_has_fresh_unified_image_artifact "$1"',
            "policy",
            image_ref,
        ],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def test_unified_source_change_requires_an_immutable_artifact_digest():
    image = "registry.example.com/vllm-unified@sha256:" + "a" * 64
    assert _policy_accepts(image)
    assert not _policy_accepts("")
    assert not _policy_accepts("registry.example.com/vllm-unified:release")
    assert not _policy_accepts("registry.example.com/vllm-unified@sha256:not-a-digest")


def _config_compare_status(before: Path, after: Path) -> int:
    return subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/lib/deploy_recreate_policy.sh; deploy_unified_image_config_changed \"$1\" \"$2\"",
            "policy",
            str(before),
            str(after),
        ],
        cwd=ROOT,
        check=False,
    ).returncode


def _config_changed(before: Path, after: Path) -> bool:
    return _config_compare_status(before, after) == 0


def test_unified_image_config_policy_detects_build_inputs(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root in (before, after):
        (root / "configs").mkdir(parents=True)
        (root / "configs" / "vllm_unified_build.yaml").write_text(
            (ROOT / "configs" / "vllm_unified_build.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    document = yaml.safe_load((after / "configs" / "vllm_unified_build.yaml").read_text(encoding="utf-8"))
    document["compatibility_pins"]["transformers"] = "5.13.2"
    (after / "configs" / "vllm_unified_build.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    assert _config_changed(before, after)


def test_unified_image_config_policy_requires_the_previous_release_to_have_the_config(tmp_path):
    # 롤백 대상은 항상 직전 release 하나이고(PREVIOUS_RELEASE = current가 가리키던
    # 곳), 이 설정 파일은 그보다 오래 존재해왔다. 직전 release에 파일이 없다면
    # 도입기 마이그레이션이 아니라 비정상이므로 "변경됨"으로 추측하지 않고 실패한다.
    before = tmp_path / "before"
    after = tmp_path / "after"
    (before / "configs").mkdir(parents=True)
    (after / "configs").mkdir(parents=True)
    (after / "configs" / "vllm_unified_build.yaml").write_text(
        (ROOT / "configs" / "vllm_unified_build.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert _config_compare_status(before, after) == 2


def test_unified_image_config_policy_fails_closed_when_configuration_is_unreadable(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root in (before, after):
        (root / "configs").mkdir(parents=True)
    (before / "configs" / "vllm_unified_build.yaml").write_text(
        (ROOT / "configs" / "vllm_unified_build.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert _config_compare_status(before, after) == 2
