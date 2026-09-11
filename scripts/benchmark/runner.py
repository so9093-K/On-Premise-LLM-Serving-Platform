"""workload 하나를 실행해 원시 결과를 만든다(ADR-0026 6·7절).

계약이 선언한 것만 실행한다. 아직 구현하지 않은 prompt 분포나 traffic mode는
조용히 근사하지 않고 거부한다. 잘못 실행된 벤치마크는 값이 없는 것보다 나쁘다.

open loop는 앞선 요청이 끝나기를 기다리지 않아야 한다. 기다리면 지연이 나빠질수록
부하가 스스로 줄어 포화가 가려지고, 그건 계약이 closed_loop라고 부르는 것이다.
그래서 dispatch를 완료와 분리하고, scheduler가 목표 시각을 못 지킨 정도를 결과에
남긴다.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from scripts.benchmark import fingerprint
from scripts.benchmark.client import RequestSample, stream_once
from scripts.benchmark.contract import ROOT, PerformanceContract, load_yaml_mapping
from scripts.lib.service_endpoint import published_base_url

# 구현한 것만 적는다. 이 집합을 늘리는 것이 Epic 6의 작업이다.
_SUPPORTED_DISTRIBUTIONS = {"fixed_synthetic"}
_SUPPORTED_TRAFFIC_MODES = {"open_loop"}

# cold cache는 매 요청이 고유 prefix를 써야 한다. 이전 프롬프트가 prefix cache에
# 남으면 처리량이 과대 측정된다.
_FILLER = "the quick brown fox jumps over the lazy dog "
# 토큰당 자수는 모델의 tokenizer와 chat template에 달려 있다. 고정값 4를 쓰면
# 512 토큰을 요청했을 때 실측 447이 나왔다(13% 부족). long_context의 24576에서는
# 3000 토큰 넘게 어긋나 측정 대상 자체가 달라진다. 그래서 기본값을 두지 않고
# 실행 시작에 두 번 재서 tokenizer를 역산한다. 보정에 실패하면 실행하지 않는다.
# 보정에 쓸 두 지점. 기울기를 얻으려면 서로 충분히 떨어져야 한다.
_CALIBRATION_CHARS = (400, 2000)
# 보정이 이 범위를 벗어나면 역산이 실패한 것이다. 조용히 쓰지 않는다.
_PLAUSIBLE_CHARS_PER_TOKEN = (1.5, 12.0)


@dataclass(frozen=True)
class RunOptions:
    workload_id: str
    mode: str
    gateway_base: str
    api_key: str
    seed: int
    max_requests: int | None = None
    measurement_seconds: float | None = None
    warmup_seconds: float | None = None
    # sweep 한 지점의 요청률. 계약이 선언한 값을 덮어쓴다. 결과 문서에는 실제로
    # 사용한 값이 기록되므로 나중에 어느 지점이었는지 되짚을 수 있다.
    request_rate_per_second: float | None = None
    sweep_id: str | None = None


class RunnerError(RuntimeError):
    """측정을 계속하면 잘못된 숫자가 나오는 상태."""


def _unsupported(workload_id: str, field: str, value: str, supported: set[str]) -> RunnerError:
    return RunnerError(
        f"workload {workload_id!r} declares {field}={value!r}; this runner implements "
        f"{sorted(supported)} only. 근사해서 실행하지 않는다."
    )


@dataclass(frozen=True)
class PromptScale:
    """prompt 토큰 수를 문자 수로 되돌리는 선형 모형.

    ``prompt_tokens = overhead + chars / chars_per_token``. overhead는 chat
    template이 사용자 내용과 무관하게 얹는 토큰이다.
    """

    chars_per_token: float
    overhead_tokens: float

    def chars_for(self, input_tokens: int) -> int:
        return max(1, round((input_tokens - self.overhead_tokens) * self.chars_per_token))


def _text_of(chars: int, rng: random.Random, index: int) -> str:
    nonce = f"session {index}-{rng.getrandbits(48):012x}. "
    body_chars = max(0, chars - len(nonce))
    repeats = body_chars // len(_FILLER) + 1
    return nonce + (_FILLER * repeats)[:body_chars]


def _prompt(rng: random.Random, input_tokens: int, index: int, scale: PromptScale) -> str:
    """input_tokens에 맞춘 고유 프롬프트. 고유하지 않으면 prefix cache가 낀다."""
    return _text_of(scale.chars_for(input_tokens), rng, index)


def _calibrate_prompt_scale(
    client: httpx.Client,
    *,
    url: str,
    headers: dict[str, str],
    model: str,
    workload_id: str,
) -> PromptScale:
    """길이가 다른 두 요청의 prompt_tokens로 tokenizer를 역산한다.

    max_tokens=1로 보내 생성 비용을 없앤다. 재는 것은 prefill 토큰 수뿐이다.
    """
    rng = random.Random(0)
    measured: list[tuple[int, int]] = []
    for probe, chars in enumerate(_CALIBRATION_CHARS):
        payload = {
            "model": model,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": _text_of(chars, rng, -1 - probe)}],
            "max_tokens": 1,
            "temperature": 0,
        }
        sample = stream_once(client, url=url, payload=payload, headers=headers, index=-1 - probe)
        if sample.client_input_tokens is None:
            raise RunnerError(
                f"calibration probe returned no prompt_tokens (status={sample.status_code}, "
                f"error={sample.error_code}); usage 없이는 프롬프트 길이를 맞출 수 없다"
            )
        measured.append((chars, int(sample.client_input_tokens)))

    (chars_low, tokens_low), (chars_high, tokens_high) = measured
    if tokens_high <= tokens_low:
        raise RunnerError(
            f"calibration probes for {workload_id!r} produced non-increasing prompt_tokens "
            f"({tokens_low} -> {tokens_high}); tokenizer를 역산할 수 없다"
        )
    chars_per_token = (chars_high - chars_low) / (tokens_high - tokens_low)
    low, high = _PLAUSIBLE_CHARS_PER_TOKEN
    if not low <= chars_per_token <= high:
        raise RunnerError(
            f"calibration produced {chars_per_token:.2f} chars per token, outside the plausible "
            f"range {low}-{high}; 추정치를 그대로 쓰면 요청 길이가 계약과 달라진다"
        )
    overhead = tokens_low - chars_low / chars_per_token
    return PromptScale(chars_per_token=chars_per_token, overhead_tokens=overhead)


def _build_payload(workload: dict[str, Any], model: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int((workload.get("output") or {}).get("max_tokens", 128)),
        "temperature": 0,
    }


def _assert_required_parameters_are_sent(workload_id: str, workload: dict[str, Any]) -> None:
    """계약이 요구한 파라미터를 실제로 보내는지 발사 전에 확인한다.

    stream_options가 빠지면 usage가 오지 않아 토큰 기준 값이 조용히 빈다. 요청을
    다 보낸 뒤에 알게 되면 그 실행 시간이 통째로 버려진다.
    """
    sent = set(_build_payload(workload, "probe", "probe"))
    missing = sorted(set(workload.get("required_request_parameters") or []) - sent)
    if missing:
        raise RunnerError(
            f"workload {workload_id!r} requires request parameters {missing} that the runner "
            "does not send; 보내지 않으면 그 지표가 조용히 빈다"
        )


def run_workload(contract: PerformanceContract, options: RunOptions) -> dict[str, Any]:
    workload = contract.workload(options.workload_id)
    prompt_spec = workload.get("prompt") or {}
    traffic = workload.get("traffic") or {}

    distribution = str(prompt_spec.get("distribution", ""))
    if distribution not in _SUPPORTED_DISTRIBUTIONS:
        raise _unsupported(options.workload_id, "prompt.distribution", distribution, _SUPPORTED_DISTRIBUTIONS)
    mode = str(traffic.get("mode", ""))
    if mode not in _SUPPORTED_TRAFFIC_MODES:
        raise _unsupported(options.workload_id, "traffic.mode", mode, _SUPPORTED_TRAFFIC_MODES)
    _assert_required_parameters_are_sent(options.workload_id, workload)
    rate = float(options.request_rate_per_second or traffic.get("request_rate_per_second", 0))
    if rate <= 0:
        raise RunnerError(f"workload {options.workload_id!r} declares no positive request_rate_per_second")

    environment = fingerprint.collect()
    model = str(environment.get("served_model_name") or environment["model_id"])
    input_tokens = int(prompt_spec.get("input_tokens", 512))
    duration = workload.get("duration") or {}
    measurement_seconds = options.measurement_seconds
    if measurement_seconds is None:
        measurement_seconds = float(duration.get("measurement_seconds", 60))
    warmup_seconds = options.warmup_seconds
    if warmup_seconds is None:
        warmup_seconds = float(duration.get("warmup_seconds", 0))

    headers = {"content-type": "application/json"}
    if options.api_key:
        headers["authorization"] = f"Bearer {options.api_key}"
    url = f"{options.gateway_base.rstrip('/')}/v1/chat/completions"

    # cold cache에서는 warmup 요청도 고유 prefix를 써야 한다. 같은 rng를 이어 쓰면
    # warmup과 측정 구간의 프롬프트가 겹치지 않는다.
    rng = random.Random(options.seed)
    counter = _Counter()

    started_at = datetime.now(timezone.utc)
    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=600.0, write=60.0, pool=5.0)) as client:

        scale = _calibrate_prompt_scale(
            client, url=url, headers=headers, model=model, workload_id=options.workload_id
        )

        def send(index: int) -> RequestSample:
            payload = _build_payload(workload, model, _prompt(rng, input_tokens, index, scale))
            return stream_once(client, url=url, payload=payload, headers=headers, index=index)

        if warmup_seconds > 0:
            # warmup 샘플은 버린다. 모델 적재와 첫 컴파일이 섞이면 측정이 오염된다.
            _dispatch_open_loop(send, counter, rate=rate, seconds=warmup_seconds, max_requests=None)
        wall_start = time.perf_counter()
        samples, dispatch_lag, dispatch_window = _dispatch_open_loop(
            send, counter, rate=rate, seconds=measurement_seconds, max_requests=options.max_requests
        )
        wall_seconds = time.perf_counter() - wall_start

    if not samples:
        raise RunnerError("측정 구간에서 요청이 하나도 나가지 않았다")

    return _result_document(
        contract=contract,
        options=options,
        workload=workload,
        environment=environment,
        samples=samples,
        started_at=started_at,
        wall_seconds=wall_seconds,
        dispatch_lag=dispatch_lag,
        dispatch_window=dispatch_window,
    )


class _Counter:
    """warmup과 측정 구간에 걸쳐 프롬프트 index를 이어 준다."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            value = self._value
            self._value += 1
            return value


def _dispatch_open_loop(
    send,
    counter: _Counter,
    *,
    rate: float,
    seconds: float,
    max_requests: int | None,
) -> tuple[list[RequestSample], float, float]:
    """목표 시각마다 요청을 띄운다. 앞선 요청의 완료를 기다리지 않는다.

    pool이 가득 차면 submit이 대기열에 쌓여 dispatch가 완료에 묶인다. 그 순간
    open loop가 아니게 되므로 조용히 계속하지 않고 실패시킨다.

    포화는 여기서 감지하지 않는다. Gateway의 admission control이 큐를 이미 막고
    503으로 흘려보내므로 in-flight가 무한히 자라는 상태에 도달하지 않는다. 실측에서
    1 rps로 181건을 보냈을 때 135건이 QUEUE_TIMEOUT으로 거절됐고 in-flight는
    단조 증가하지 않았다. 포화는 성공률과 error_code로 결과에 그대로 남는다.
    """
    planned = max_requests if max_requests is not None else math.ceil(rate * seconds)
    # 모든 요청이 끝까지 안 끝나도 dispatch를 막지 않을 만큼 넉넉히 잡는다.
    workers = max(4, min(planned, 512))
    samples: list[RequestSample] = []
    lock = threading.Lock()
    inflight = threading.Semaphore(workers)
    max_lag = 0.0

    failures: list[BaseException] = []

    def task(index: int) -> None:
        # future를 확인하지 않으므로 여기서 삼키면 예외가 사라진다. 실제로
        # 계약 위반이 "요청이 하나도 나가지 않았다"로 잘못 보고됐다.
        try:
            sample = send(index)
        except BaseException as exc:  # noqa: BLE001 - 원인을 잃지 않는 것이 목적이다
            with lock:
                failures.append(exc)
            return
        finally:
            inflight.release()
        with lock:
            samples.append(sample)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="perf") as pool:
        start = time.perf_counter()
        first_dispatch = last_dispatch = start
        dispatched = 0
        while True:
            if max_requests is not None and dispatched >= max_requests:
                break
            if max_requests is None and time.perf_counter() - start >= seconds:
                break
            target = start + dispatched / rate
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            if not inflight.acquire(blocking=False):
                raise RunnerError(
                    f"{workers} requests still in flight; the harness cannot keep the open-loop "
                    "schedule, so the numbers would describe the harness rather than the server"
                )
            now = time.perf_counter()
            max_lag = max(max_lag, now - target)
            if dispatched == 0:
                first_dispatch = now
            last_dispatch = now
            pool.submit(task, counter.next())
            dispatched += 1

    # 부하를 가한 구간이다. 그 뒤는 tail drain이라 처리량의 분모가 아니다.
    # 요청 하나가 inter-arrival 간격 하나를 차지하므로 마지막 간격을 더한다.
    # 빼면 N개를 (N-1)/rate로 나누게 되어 처리량이 과대 평가된다.
    dispatch_window = (last_dispatch - first_dispatch) + 1.0 / rate if dispatched else 0.0
    if failures:
        raise RunnerError(f"{len(failures)} requests failed before reaching the server") from failures[0]
    samples.sort(key=lambda sample: sample.index)
    return samples, max_lag, dispatch_window


def _result_document(
    *,
    contract: PerformanceContract,
    options: RunOptions,
    workload: dict[str, Any],
    environment: dict[str, Any],
    samples: list[RequestSample],
    started_at: datetime,
    wall_seconds: float,
    dispatch_lag: float,
    dispatch_window: float,
) -> dict[str, Any]:
    traffic = workload.get("traffic") or {}
    traffic_document: dict[str, Any] = {"mode": str(traffic.get("mode"))}
    # 계약이 선언한 값이 아니라 이 실행이 실제로 건 부하를 적는다. sweep 지점은
    # 계약값과 다르고, 결과를 해석할 때 필요한 것은 실제로 건 쪽이다.
    rate = options.request_rate_per_second or traffic.get("request_rate_per_second")
    if rate is not None:
        traffic_document["request_rate_per_second"] = float(rate)
    admission = (traffic.get("admission_limit") or {}).get("max_concurrency")
    if admission:
        traffic_document["admission_limit"] = int(admission)

    cache = workload.get("cache") or {}
    workload_document: dict[str, Any] = {
        "id": options.workload_id,
        "cache_policy": str(cache.get("policy")),
        "traffic": traffic_document,
    }
    if "shared_prefix_ratio" in cache:
        workload_document["shared_prefix_ratio"] = float(cache["shared_prefix_ratio"])

    return {
        "schema_version": 1,
        "contract_version": contract.version,
        "run": {
            "id": f"{options.workload_id}-{started_at:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
            "mode": options.mode,
            "started_at": started_at.isoformat().replace("+00:00", "Z"),
            "duration_seconds": round(wall_seconds, 3),
            "dispatch_window_seconds": round(dispatch_window, 3),
            "seed": options.seed,
            "measurement_backend": "platform_native_http",
            "max_dispatch_lag_seconds": round(dispatch_lag, 6),
            **({"sweep_id": options.sweep_id} if options.sweep_id else {}),
        },
        "environment": environment,
        "workload": workload_document,
        "requests": [sample.as_document() for sample in samples],
        # summary와 verdict는 evaluator가 채운다(Epic 5). 여기서는 수집만 한다.
        "summary": {},
    }


def gateway_endpoint(override: str = "") -> tuple[str, str]:
    """Gateway 주소를 services.yaml에서, 키를 기존 env 규약에서 읽는다.

    주소 해석은 runtime validation과 같은 helper를 쓴다. 두 도구가 서로 다른
    Gateway를 재면 비교가 성립하지 않는다.
    """
    base = override.strip()
    if not base:
        services = load_yaml_mapping(ROOT / "configs" / "services.yaml")["services"]
        base = published_base_url(services, "gateway")
    api_key = os.getenv("API_KEY", "").strip()
    if not api_key:
        api_key = os.getenv("API_KEYS", "").split(",")[0].strip()
    return base.rstrip("/"), api_key
