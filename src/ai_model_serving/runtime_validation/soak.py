from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .config import RuntimeValidationConfig
from .http_client import RuntimeValidationHttpClient
from .results import CheckResult


class SoakRunner:
    """live runtime validation에서 사용하는 동시성 smoke loop다."""

    def __init__(self, *, config: RuntimeValidationConfig, http: RuntimeValidationHttpClient) -> None:
        self.config = config
        self.http = http
        self.gateway_base = config.gateway_base
        self.risk_base = config.risk_base

    def run_once(self, index: int) -> dict[str, Any]:
        started = time.monotonic()
        self.http.json("POST", f"{self.risk_base}/v1/risk/assessments", {"prompt": f"runtime validation prompt {index}"}, internal=True)
        self.http.json("POST", f"{self.gateway_base}/v1/embeddings", {"model": "local-embed", "input": [f"embedding {index}"]})
        self.http.json("POST", f"{self.gateway_base}/v1/chat/completions", {"model": "local-main", "messages": [{"role": "user", "content": "Say OK only."}], "max_tokens": 1, "temperature": 0})
        return {"iteration": index, "latency_ms": int((time.monotonic() - started) * 1000)}

    def run(self) -> CheckResult:
        if self.config.skip_soak:
            return CheckResult("gpu-capacity", "soak test", "pass", detail="skipped by --skip-soak", details={"skipped": True})
        deadline = time.monotonic() + self.config.soak_seconds
        iteration = 0
        latencies: list[int] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=self.config.concurrency) as pool:
            while time.monotonic() < deadline:
                futures = [pool.submit(self.run_once, iteration + offset) for offset in range(self.config.concurrency)]
                iteration += self.config.concurrency
                for future in as_completed(futures):
                    try:
                        latencies.append(future.result()["latency_ms"])
                    except Exception as exc:  # noqa: BLE001 - failure detail belongs in validation details.
                        errors.append(f"{type(exc).__name__}: {exc}")
                time.sleep(self.config.soak_interval_seconds)
        ok = not errors and bool(latencies)
        return CheckResult("gpu-capacity", "soak test", "pass" if ok else "fail", details={"iterations": iteration, "errors": errors[:10], "max_latency_ms": max(latencies) if latencies else None, "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None})
