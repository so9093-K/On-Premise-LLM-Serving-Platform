"""선언된 모든 배포 구성의 지문 경로를 돌린다(ADR-0026 8절).

이 파일이 있는 이유. 이 저장소에서 실행할 수 없는 구성의 코드 경로를 한 번도
돌려보지 않고 "그 장비에서 확인하면 된다"로 미뤘다. 미룬 동안 두 가지가 깨져
있었다 -- vLLM의 실행 설정이 전부 null이었고, Linux target 결과에 Apple 칩이
가속기로 적혔다. 하드웨어가 없어도 둘 다 여기서 드러났다.
"""

from __future__ import annotations

import platform

import pytest

from scripts.benchmark import fingerprint
from scripts.benchmark.contract import ROOT, load_yaml_mapping


def _targets() -> dict:
    document = load_yaml_mapping(ROOT / "configs/deployment_targets.yaml")
    return document.get("targets") or document


@pytest.mark.parametrize("target_id", sorted(_targets()))
def test_every_declared_target_produces_a_usable_fingerprint(target_id, monkeypatch):
    """선언된 구성 중 하나라도 지문을 못 만들면 그 구성에서는 결과를 남길 수 없다."""
    monkeypatch.setenv("DEPLOYMENT_TARGET", target_id)
    document = fingerprint.collect()
    for field in ("git_commit", "deployment_target", "runtime_backend", "runtime_profile",
                  "model_id", "model_revision", "platform_image", "runtime_flags"):
        assert document.get(field), f"{target_id}: {field}"
    # 실행 설정이 비어 있으면 6개월 뒤 이 숫자를 해석할 수 없다. vLLM은 값이
    # profile 최상위가 아니라 실행 명령줄에 있어 전부 None이던 적이 있다.
    flags = document["runtime_flags"]
    assert flags and all(value is not None for value in flags.values()), flags


@pytest.mark.parametrize("target_id", sorted(_targets()))
def test_the_accelerator_is_never_borrowed_from_the_client_machine(target_id, monkeypatch):
    """가속기는 런타임이 있는 곳의 것이다.

    benchmark client는 원격 Gateway를 향해 돌 수 있다. 이 기기의 가속기를 적으면
    Linux target 결과에 Apple 칩이 들어간다 -- 실제로 그랬다.
    """
    monkeypatch.setenv("DEPLOYMENT_TARGET", target_id)
    declared = str(_targets()[target_id]["platform"])
    local = {"Darwin": "macos", "Linux": "linux"}.get(platform.system(), "")
    document = fingerprint.collect()

    if declared == local:
        gpu = document.get("gpu")
        if gpu is not None:
            expected = "unified" if declared == "macos" else "dedicated"
            assert gpu["memory_kind"] == expected
    else:
        assert document.get("gpu") is None
        # 조용히 비우면 가속기가 없는 것으로 읽힌다.
        assert document["accelerator_unavailable_reason"]


def test_a_vllm_profile_without_declared_flags_is_refused(monkeypatch):
    """명령줄에 설정이 없으면 해석 불가한 결과를 만들지 않고 멈춘다."""
    monkeypatch.setenv("DEPLOYMENT_TARGET", "linux-nvidia-dynamic")
    monkeypatch.setattr(
        fingerprint, "load_yaml_mapping",
        lambda path: {
            "default_profile": "bare",
            "profiles": {"bare": {"model_id": "m", "revision": "r", "command": []}},
            "targets": {"linux-nvidia-dynamic": {"platform": "linux", "runtime_backend": "vllm-cuda"}},
        },
    )
    with pytest.raises(RuntimeError, match="does not declare"):
        fingerprint.collect()
