"""벤치마크 harness가 스스로를 재지 않는지 고정한다(ADR-0026 6·7절).

여기 있는 테스트는 전부 실제로 발견한 오측정에서 나왔다.

- usage 전용 chunk와 ``[DONE]``의 도착 간격까지 출력 chunk 간격으로 세어, content
  chunk 5개에 대해 간격이 6개 나왔고 마지막 값이 21마이크로초였다.
- dispatch를 완료와 묶으면 서버가 느려질수록 부하가 스스로 줄어 포화가 가려진다.
- 처리량 분모를 전체 벽시계로 잡으면 tail drain 때문에 1.0 rps가 0.7로 보인다.

httpx mock 대신 실제 SSE 서버를 띄운다. 시간 측정은 소켓에서 나오는 값이라
transport를 가짜로 바꾸면 재려던 것이 사라진다.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from scripts.benchmark.client import RequestSample, stream_once
from scripts.benchmark.contract import PerformanceContract
from scripts.benchmark.runner import RunOptions, RunnerError, run_workload

CONTENT_CHUNKS = 5
CHUNK_GAP_SECONDS = 0.02
# 가짜 tokenizer. runner의 보정이 이 값을 역산해 낼 수 있어야 한다.
CHARS_PER_TOKEN = 5
TEMPLATE_OVERHEAD_TOKENS = 17
# 보정 probe 두 건은 측정 샘플이 아니다.
CALIBRATION_REQUESTS = 2


def _sse_handler(*, server_delay: float, seen: list[float], lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # 테스트 출력을 더럽히지 않는다
            pass

        def _chunk(self, payload: bytes) -> None:
            self.wfile.write(f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
            self.wfile.flush()

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            content = body["messages"][0]["content"]
            # 실제 tokenizer처럼 길이에 비례해 센다. 고정값을 돌려주면 runner의
            # 프롬프트 길이 보정이 tokenizer를 역산할 수 없다.
            prompt_tokens = TEMPLATE_OVERHEAD_TOKENS + len(content) // CHARS_PER_TOKEN
            with lock:
                seen.append(time.perf_counter())
            time.sleep(server_delay)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("x-request-id", "req-test")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            for chunk in range(CONTENT_CHUNKS):
                if chunk:
                    # 마지막 토큰 뒤에는 쉬지 않는다. 실제 서버도 그렇다.
                    time.sleep(CHUNK_GAP_SECONDS)
                self._chunk(b'data: {"id":"cmpl-1","choices":[{"delta":{"content":"hi "}}]}\n\n')
            # 마지막 두 줄은 출력 토큰을 싣지 않는다. 간격으로 세면 안 된다.
            usage = {"prompt_tokens": prompt_tokens, "completion_tokens": CONTENT_CHUNKS}
            self._chunk(
                f'data: {{"id":"cmpl-1","choices":[],"usage":{json.dumps(usage)}}}\n\n'.encode()
            )
            self._chunk(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    return Handler


@pytest.fixture
def sse_server():
    """요청 도착 시각을 기록하는 SSE 서버를 띄운다."""
    started: list[float] = []
    lock = threading.Lock()

    def start(server_delay: float = 0.0) -> tuple[str, list[float]]:
        handler = _sse_handler(server_delay=server_delay, seen=started, lock=lock)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", started

    servers: list[ThreadingHTTPServer] = []
    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_only_content_chunks_count_as_output_chunks(sse_server):
    """usage chunk와 [DONE]의 도착 간격은 출력 chunk 간격이 아니다."""
    base, _ = sse_server()
    with httpx.Client(timeout=10.0) as client:
        sample = stream_once(
            client,
            url=f"{base}/v1/chat/completions",
            payload={"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            headers={},
            index=0,
        )
    assert sample.succeeded
    # content chunk가 5개면 간격은 4개다. usage 전용 chunk와 [DONE]까지 세면 6개가
    # 된다. 개수만으로 충분하고, 간격의 크기는 재지 않는다 -- 부하가 걸린 머신에서는
    # client가 두 chunk를 한 번에 읽어 간격이 0에 가까워질 수 있다.
    assert len(sample.client_time_per_output_chunk_seconds) == CONTENT_CHUNKS - 1
    assert sample.client_output_tokens == CONTENT_CHUNKS
    assert sample.upstream_response_id == "cmpl-1"


def test_token_based_value_covers_the_whole_generation_span():
    """토큰 기준 근사값은 chunk 도착 패턴이 아니라 생성 구간 전체에서 나온다.

    벽시계를 재지 않는다. 부하가 걸린 머신에서 절대값을 상수와 비교하면 측정이
    아니라 그 머신의 여유를 재게 된다.
    """
    sample = RequestSample(
        index=0,
        succeeded=True,
        client_time_to_first_chunk_seconds=1.0,
        client_operation_duration_seconds=5.0,
        # 뭉쳐 도착해 간격은 들쭉날쭉하지만 근사값은 이 값들을 보지 않는다.
        client_time_per_output_chunk_seconds=[0.0001, 0.0001, 3.9998],
        client_output_tokens=5,
    )
    # 생성 구간 4.0초를 첫 토큰을 뺀 4개 토큰으로 나눈다.
    assert sample.client_time_per_output_token_seconds == pytest.approx(1.0)


def test_token_based_value_is_absent_when_it_cannot_be_computed():
    """토큰이 하나뿐이면 간격이 존재하지 않는다. 0으로 채우지 않는다."""
    for tokens in (None, 0, 1):
        sample = RequestSample(
            index=0,
            succeeded=True,
            client_time_to_first_chunk_seconds=1.0,
            client_operation_duration_seconds=5.0,
            client_output_tokens=tokens,
        )
        assert sample.client_time_per_output_token_seconds is None, tokens


def _contract(workload: dict[str, Any]) -> PerformanceContract:
    return PerformanceContract(version=1, metrics={}, workloads={"w": workload}, slo_classes={})


def _open_loop_workload(rate: float) -> dict[str, Any]:
    return {
        "prompt": {"distribution": "fixed_synthetic", "input_tokens": 8},
        "output": {"max_tokens": 16},
        "required_request_parameters": ["stream", "stream_options", "max_tokens", "temperature"],
        "traffic": {"mode": "open_loop", "request_rate_per_second": rate},
        "cache": {"policy": "cold"},
        "duration": {"warmup_seconds": 0, "measurement_seconds": 1},
    }


def _options(base: str, **overrides: Any) -> RunOptions:
    defaults: dict[str, Any] = {
        "workload_id": "w",
        "mode": "smoke",
        "gateway_base": base,
        "api_key": "",
        "seed": 1,
        "max_requests": 4,
        "warmup_seconds": 0,
    }
    defaults.update(overrides)
    return RunOptions(**defaults)


def test_open_loop_keeps_sending_while_the_server_is_slow(sse_server, monkeypatch):
    """앞선 요청이 끝나기를 기다리면 그건 closed loop다. 포화가 가려진다."""
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    rate = 10.0
    server_delay = 0.4
    base, arrivals = sse_server(server_delay=server_delay)

    document = run_workload(_contract(_open_loop_workload(rate)), _options(base))

    assert len(document["requests"]) == 4
    measured = arrivals[CALIBRATION_REQUESTS:]
    span = measured[-1] - measured[0]
    # 완료를 기다렸다면 요청 사이가 server_delay만큼 벌어져 span이 3배 이상이 된다.
    # 느린 머신은 span을 늘리기만 하므로, 이 한쪽 부등식은 jitter에 흔들리지 않는다.
    closed_loop_span = 3 * server_delay
    assert span < closed_loop_span, f"dispatch span {span:.3f}s >= closed loop {closed_loop_span:.3f}s"
    assert document["run"]["max_dispatch_lag_seconds"] >= 0


def test_throughput_denominator_excludes_the_tail_drain(sse_server, monkeypatch):
    """전체 벽시계로 나누면 마지막 요청을 기다린 시간까지 분모에 들어간다."""
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    rate = 10.0
    server_delay = 0.4
    base, _ = sse_server(server_delay=server_delay)

    run = run_workload(_contract(_open_loop_workload(rate)), _options(base))["run"]

    # 분모를 duration으로 되돌리면 마지막 요청을 기다린 시간까지 들어가 처리량이
    # 과소 평가된다. 느린 머신은 duration만 늘리므로 이 부등식은 흔들리지 않는다.
    assert run["dispatch_window_seconds"] < run["duration_seconds"]
    n = 4
    assert n / run["duration_seconds"] < n / run["dispatch_window_seconds"]


@pytest.mark.parametrize(
    "patch,expected",
    [
        ({"prompt": {"distribution": "multi_turn_session", "input_tokens": 8}}, "prompt.distribution"),
        # 계약이 새 traffic mode를 선언해도 근사해서 돌리지 않는다.
        ({"traffic": {"mode": "poisson_arrivals", "request_rate_per_second": 1.0}}, "traffic.mode"),
    ],
)
def test_unimplemented_workload_shapes_are_refused_not_approximated(patch, expected, monkeypatch):
    """근사 실행은 값이 없는 것보다 나쁘다. 그 숫자가 근거로 쓰이기 때문이다."""
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    workload = _open_loop_workload(1.0) | patch

    with pytest.raises(RunnerError) as error:
        run_workload(_contract(workload), _options("http://127.0.0.1:1"))
    assert expected in str(error.value)


def test_contract_required_parameters_must_actually_be_sent(monkeypatch):
    """stream_options가 빠지면 usage가 오지 않아 토큰 기준 값이 조용히 빈다."""
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    workload = _open_loop_workload(1.0)
    workload["required_request_parameters"] = ["stream", "logprobs"]

    with pytest.raises(RunnerError) as error:
        run_workload(_contract(workload), _options("http://127.0.0.1:1"))
    assert "logprobs" in str(error.value)


def test_cold_cache_workload_never_repeats_a_prompt(sse_server, monkeypatch):
    """같은 prefix가 반복되면 prefix cache가 처리량을 부풀린다."""
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    from scripts.benchmark import runner

    prompts: list[str] = []
    original = runner._build_payload

    def capture(workload, model, prompt):
        prompts.append(prompt)
        return original(workload, model, prompt)

    monkeypatch.setattr(runner, "_build_payload", capture)
    base, _ = sse_server()
    runner.run_workload(
        _contract(_open_loop_workload(20.0)), _options(base, max_requests=3, warmup_seconds=None)
    )

    assert len(prompts) == len(set(prompts))


def test_a_failure_inside_a_worker_thread_is_not_swallowed(sse_server, monkeypatch):
    """future를 확인하지 않으므로 worker에서 삼키면 원인이 사라진다.

    실제로 계약 위반이 "측정 구간에서 요청이 하나도 나가지 않았다"로 보고됐고,
    그 메시지로는 무엇을 고쳐야 하는지 알 수 없었다.
    """
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    from scripts.benchmark import runner

    def explode(*args, **kwargs):
        raise ValueError("tokenizer is missing")

    # 발사 전 계약 검사가 아니라 worker 안에서만 터지는 지점을 고른다.
    monkeypatch.setattr(runner, "_prompt", explode)
    base, _ = sse_server()

    with pytest.raises(RunnerError) as error:
        runner.run_workload(_contract(_open_loop_workload(20.0)), _options(base, max_requests=2))
    assert "failed before reaching the server" in str(error.value)
    assert isinstance(error.value.__cause__, ValueError)


def test_a_length_beyond_the_live_runtime_context_is_refused_before_sending(monkeypatch):
    """담지 못하는 길이를 보내면 요청 단위로 거부되어 측정이 아니라 거부를 재게 된다.

    계약은 profile 단위로 선언하지만, 실제로 돌고 있는 런타임의 값은 실행 시점에만
    알 수 있다. gemma4-26b-a4b-fp8은 20,000이고 계약은 24,704를 요구한다.
    """
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    from scripts.benchmark import runner

    monkeypatch.setattr(
        runner.fingerprint, "collect",
        lambda: {"model_id": "m", "runtime_flags": {"max_kv_size": 20000}},
    )
    workload = _open_loop_workload(1.0)
    workload["prompt"] = {"distribution": "length_sweep", "input_tokens_sweep": [24576]}
    workload["output"] = {"max_tokens": 128}

    with pytest.raises(RunnerError, match="context tokens"):
        runner.run_workload(_contract(workload), _options("http://127.0.0.1:1", input_tokens=24576))
