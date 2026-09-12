"""접근 로그 조인을 고정한다(ADR-0026 Epic 4).

Gateway는 admission 대기 시간을 metric으로 내보내지 않는다. 응답 헤더로 내보내면
내부 타이밍이 공개 API가 되므로 로그에만 남긴다. client가 기록한 request_id가
조인 키다.
"""

from __future__ import annotations

import json

import pytest

from scripts.benchmark.request_events import (
    RequestEventsUnavailable,
    admission_waits,
    attach,
    attach_with_retry,
    log_directory,
)


def _write(directory, name: str, rows: list[dict]) -> None:
    directory.joinpath(name).write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def _document(request_ids: list[str | None]) -> dict:
    return {
        "run": {"id": "r"},
        "requests": [
            {"index": i, "succeeded": True, **({"request_id": rid} if rid else {})}
            for i, rid in enumerate(request_ids)
        ],
    }


def test_the_log_directory_comes_from_the_shell_declaration():
    """경로를 여기 다시 적으면 배치를 바꿀 때 한쪽만 고치게 된다."""
    from scripts.benchmark.contract import ROOT

    directory = log_directory()
    assert directory.name == "request-events"
    # 선언이 실제로 그 파일에 있어야 한다. 없으면 조용히 기본값으로 흘러간다.
    declaration = ROOT / "scripts/lib/gateway_runtime_state.sh"
    assert f'REQUEST_EVENT_LOG_DIR_RELPATH=".runtime/{directory.name}"' in declaration.read_text(
        encoding="utf-8"
    )


def test_milliseconds_become_seconds():
    """계약은 초를 쓴다(Prometheus base unit 규약). 로그는 ms다."""
    waits = {"req-a": 5.02072}
    document = attach(_document(["req-a"]), waits)
    assert document["requests"][0]["gateway_admission_wait_seconds"] == pytest.approx(5.02072)


def test_only_lines_with_the_field_are_read(tmp_path):
    """로그는 이 벤치마크만 쓰는 파일이 아니다. 다른 줄이 섞여 있다."""
    _write(tmp_path, "gateway.jsonl", [
        {"request_id": "req-a", "queue_wait_ms": 1500.0, "route": "/v1/chat/completions"},
        {"request_id": "req-b", "route": "/health"},
        {"message": "startup", "level": "INFO"},
    ])
    assert admission_waits(tmp_path) == {"req-a": 1.5}


def test_rotated_files_are_read_too(tmp_path):
    """측정 구간의 앞부분이 회전한 파일에 남아 있을 수 있다."""
    _write(tmp_path, "gateway.jsonl", [{"request_id": "new", "queue_wait_ms": 200.0}])
    _write(tmp_path, "gateway.jsonl.1", [{"request_id": "old", "queue_wait_ms": 100.0}])
    assert admission_waits(tmp_path) == {"old": 0.1, "new": 0.2}


def test_a_malformed_line_does_not_stop_the_join(tmp_path):
    """로그는 추가되는 중이다. 마지막 줄이 반만 쓰여 있을 수 있다."""
    path = tmp_path / "gateway.jsonl"
    path.write_text('{"request_id": "req-a", "queue_wait_ms": 300.0}\n{"request_id": "req-b", "queue_wa',
                    encoding="utf-8")
    assert admission_waits(tmp_path) == {"req-a": 0.3}


def test_the_joined_count_is_recorded_when_some_requests_are_missing(tmp_path):
    """조용히 일부만 채우면 그 부분집합으로 계산한 값이 전체를 대표하는 것처럼 읽힌다."""
    _write(tmp_path, "gateway.jsonl", [{"request_id": "req-a", "queue_wait_ms": 100.0}])
    document = attach_with_retry(_document(["req-a", "req-b"]), directory=tmp_path,
                                 timeout_seconds=0.1, interval_seconds=0.01)
    assert document["run"]["admission_wait_joined_requests"] == 1
    assert "gateway_admission_wait_seconds" in document["requests"][0]
    assert "gateway_admission_wait_seconds" not in document["requests"][1]


def test_a_missing_log_is_raised_not_treated_as_zero_wait(tmp_path):
    """대기가 0이었던 것과 조인할 로그를 못 찾은 것은 다르다."""
    with pytest.raises(RequestEventsUnavailable, match="no gateway"):
        admission_waits(tmp_path)


def test_the_join_waits_for_a_line_that_has_not_been_flushed_yet(tmp_path):
    """Gateway는 요청이 끝난 뒤에 한 줄을 쓴다. 바로 읽으면 마지막 줄이 없다.

    실제로 4건 중 3건만 이어졌다. 데이터가 없는 것이 아니라 이르게 읽은 것이다.
    """
    _write(tmp_path, "gateway.jsonl", [{"request_id": "req-a", "queue_wait_ms": 100.0}])
    calls = {"n": 0}
    real = admission_waits

    def appearing_late(directory=None):
        calls["n"] += 1
        if calls["n"] >= 2:
            _write(tmp_path, "gateway.jsonl", [
                {"request_id": "req-a", "queue_wait_ms": 100.0},
                {"request_id": "req-b", "queue_wait_ms": 200.0},
            ])
        return real(tmp_path)

    import scripts.benchmark.request_events as module

    original = module.admission_waits
    module.admission_waits = appearing_late
    try:
        document = attach_with_retry(_document(["req-a", "req-b"]), directory=tmp_path,
                                     timeout_seconds=2.0, interval_seconds=0.01)
    finally:
        module.admission_waits = original
    assert document["run"]["admission_wait_joined_requests"] == 2
