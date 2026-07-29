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


def test_unified_image_config_policy_ignores_unrelated_images_but_detects_build_inputs(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    for root in (before, after):
        (root / "configs").mkdir(parents=True)
        (root / "configs" / "recommended_images.yaml").write_text(
            (ROOT / "configs" / "recommended_images.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    document = yaml.safe_load((after / "configs" / "recommended_images.yaml").read_text(encoding="utf-8"))
    document["images"]["grafana"]["default"] = "grafana/grafana:next"
    (after / "configs" / "recommended_images.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    assert not _config_changed(before, after)

    document["images"]["vllm"]["compatibility_pins"]["transformers"] = "5.13.2"
    (after / "configs" / "recommended_images.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    assert _config_changed(before, after)


def test_unified_image_config_policy_fails_closed_when_configuration_is_unreadable(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()

    assert _config_compare_status(before, after) == 2
