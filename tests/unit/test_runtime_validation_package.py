"""runtime_validation 패키지의 실제 live validation 보조 결정 함수만 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ai_model_serving.runtime_validation import load_runtime_config, render_vllm_command
from ai_model_serving.runtime_validation.config import _localhost_base
from ai_model_serving.runtime_validation.reporting import write_reports
from ai_model_serving.runtime_validation.results import CheckResult

ROOT = Path(__file__).resolve().parents[2]

def _clear_runtime_endpoint_env(monkeypatch) -> None:
    # 개발자가 bootstrap/init-env-compose로 .env를 만들었어도 단위 테스트는
    # 외부 설정에 영향을 받지 않아야 한다. 프로젝트 dotenv loader는 이미 process
    # environment를 덮어쓰지 않으므로, 빈 문자열을 먼저 넣어 .env 값 유입을 막되
    # config loader의 `or default` fallback은 그대로 검증한다.
    for key in [
        "GATEWAY_BASE_URL",
        "RISK_ADAPTER_BASE_URL",
        "MAIN_LLM_BASE_URL",
        "EMBEDDING_BASE_URL",
        "RISK_PROMPT_BASE_URL",
        "PROMETHEUS_BASE_URL",
    ]:
        monkeypatch.setenv(key, "")


def test_runtime_validation_endpoint_priority_cli_env_default(monkeypatch) -> None:
    _clear_runtime_endpoint_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://env-gateway:9999")

    args = SimpleNamespace(
        root=str(ROOT),
        output_dir="reports/runtime",
        gateway_base="http://cli-gateway:7777",
        risk_base=None,
        main_llm_base=None,
        embedding_base=None,
        risk_prompt_base=None,
        prometheus_base=None,
        api_key="",
        admin_api_key="",
        timeout_seconds=30,
        soak_seconds=1800,
        soak_interval_seconds=1.0,
        concurrency=1,
        skip_soak=False,
        allow_failures=False,
    )

    config = load_runtime_config(args)
    assert config.gateway_base == "http://cli-gateway:7777"

    args.gateway_base = None
    config = load_runtime_config(args)
    assert config.gateway_base == "http://env-gateway:9999"
    assert config.risk_base == "http://localhost:9405"
    assert _localhost_base({"default_host_port": 19400}, "/v1") == "http://localhost:19400/v1"


def test_render_vllm_command_stays_openai_compatible() -> None:
    cfg = {
        "name": "example/model",
        "served_model_name": "local-main",
        "port": 9401,
        "max_model_len": 8192,
        "max_num_seqs": 1,
        "max_num_batched_tokens": 4096,
        "gpu_memory_utilization": 0.58,
        "optimization_level": 3,
        "compilation_config": {"mode": 3},
        "tensor_parallel_size": 1,
        "dtype": "half",
        "quantization": "awq",
        "trust_remote_code": True,
        "runtime_features": {
            "prefix_caching": {"enabled": True, "hash_algo": "sha256_cbor"},
            "tool_calling": {"enabled": True, "tool_call_parser": "gemma4"},
            "structured_outputs": {"enabled": True, "backend": "xgrammar", "disable_any_whitespace": True, "enable_in_reasoning": True},
        },
    }
    command = render_vllm_command("main_llm", cfg)
    assert command[:3] == ["python", "-m", "vllm.entrypoints.openai.api_server"]
    assert "--served-model-name" in command
    assert "local-main" in command
    assert "--enable-prefix-caching" in command
    assert "--enable-auto-tool-choice" in command
    assert command[command.index("--optimization-level") + 1] == "3"
    assert command[command.index("--compilation-config") + 1] == '{"mode":3}'
    structured_index = command.index("--structured-outputs-config")
    assert json.loads(command[structured_index + 1]) == {"backend": "xgrammar", "disable_any_whitespace": True, "enable_in_reasoning": True}


def test_render_vllm_command_respects_model_config_quantization() -> None:
    cfg = {
        "name": "example/fp8-compressed",
        "served_model_name": "local-main",
        "port": 9401,
        "max_model_len": 32768,
        "max_num_seqs": 1,
        "max_num_batched_tokens": 32768,
        "gpu_memory_utilization": 0.66,
        "tensor_parallel_size": 1,
        "dtype": "auto",
        "quantization": "compressed-tensors",
        "quantization_source": "model_config",
        "optimization_level": 3,
    }
    command = render_vllm_command("main_llm", cfg)
    assert "--quantization" not in command
    assert "--optimization-level" in command


def test_runtime_validation_report_does_not_store_raw_model_text(tmp_path: Path) -> None:
    json_path, md_path = write_reports(
        root=tmp_path,
        output_dir="reports/runtime",
        version="0.1.0-test",
        session_started="2026-05-09T00:00:00+00:00",
        mode="live",
        results=[CheckResult("gateway-runtime", "gateway /health", "pass", detail="ok")],
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"] == {"passed": 1, "failed": 0, "degraded_features": []}
    markdown = md_path.read_text(encoding="utf-8")
    assert "Degraded features: 없음" in markdown
    assert "원문 프롬프트, 사용자 텍스트, 모델 출력" in markdown


def test_runtime_validation_report_summarizes_degraded_features(tmp_path: Path) -> None:
    json_path, md_path = write_reports(
        root=tmp_path,
        output_dir="reports/runtime",
        version="0.1.0-test",
        session_started="2026-05-09T00:00:00+00:00",
        mode="live",
        results=[
            CheckResult(
                "json-schema-with-reasoning-canary",
                "json_schema with reasoning",
                "fail",
                details={"feature_degraded_on_failure": "json_schema_with_reasoning"},
            )
        ],
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["degraded_features"] == ["json_schema_with_reasoning"]
    assert "Degraded features: json_schema_with_reasoning" in md_path.read_text(encoding="utf-8")


def test_gpu_check_uses_hard_minimum_with_legacy_fallback(monkeypatch) -> None:
    from ai_model_serving.runtime_validation.gpu_checks import sample_gpu
    from types import SimpleNamespace

    def fake_check_output(*args, **kwargs):
        return "49140, 45000, 80, 55, 120\n"

    monkeypatch.setattr("ai_model_serving.runtime_validation.gpu_checks.subprocess.check_output", fake_check_output)

    config = SimpleNamespace(timeout_seconds=5)
    result = sample_gpu(
        config,
        {"gpu": {"reserve_gib": {"hard_minimum": 3.5}}},
        "gpu sample",
    )
    assert result.status == "pass"
    assert result.details["minimum_reserve_gib"] == 3.5

    legacy_result = sample_gpu(
        config,
        {"gpu": {"reserve_gib": {"minimum": 3.0}}},
        "gpu sample legacy",
    )
    assert legacy_result.status == "pass"
    assert legacy_result.details["minimum_reserve_gib"] == 3.0


def test_live_evidence_bundle_combines_static_and_runtime_reports(tmp_path: Path) -> None:
    from ai_model_serving.live_evidence import (
        latest_runtime_validation_report,
        live_evidence_bundle_document,
        live_evidence_bundle_markdown,
        write_live_evidence_bundle,
    )

    operator_status = {
        "runtime_targets": [
            {"logical_id": "local-main", "service_key": "main_llm"},
            {"logical_id": "local-embed", "service_key": "embedding"},
        ],
        "compose_service_regex": "main-llm-vllm|embedding-vllm",
        "monitoring_projection": {"prometheus_scrape_jobs": ["gateway", "vllm-runtimes"]},
        "readiness_vocabulary": {"statuses": ["ready", "degraded", "not_ready"]},
    }
    runtime_report = {
        "mode": "live",
        "summary": {"passed": 5, "failed": 0},
        "started_at": "2026-05-09T00:00:00+00:00",
        "finished_at": "2026-05-09T00:01:00+00:00",
        "results": [
            {"category": "gateway-runtime", "name": "gateway /health", "status": "pass", "latency_ms": 1, "details": {"status": "ok"}},
            {"category": "risk-adapter-runtime", "name": "risk /ready", "status": "pass", "latency_ms": 2, "details": {"status": "ready"}},
            {"category": "vllm-runtime", "name": "chat", "status": "pass", "latency_ms": 3, "details": {"model": "local-main", "prompt": "redacted"}},
            {"category": "monitoring-scrape", "name": "prometheus", "status": "pass", "latency_ms": 4, "details": {"up_jobs": ["gateway"]}},
            {"category": "gpu-capacity", "name": "gpu sample", "status": "pass", "latency_ms": 5, "details": {"reserve_gib": 9.5}},
        ],
    }
    doc = live_evidence_bundle_document(
        operator_status=operator_status,
        runtime_report=runtime_report,
        runtime_report_path="reports/runtime/runtime_validation_live.json",
        version="0.1.0-test",
    )
    assert doc["evidence_status"] == "live_validated"
    assert doc["privacy_contract"]["runtime_details_are_sanitised"] is True
    assert "prompt" not in doc["runtime_results"][2]["details"]
    assert doc["runtime_category_summary"]["vllm-runtime"] == {"passed": 1, "failed": 0}
    markdown = live_evidence_bundle_markdown(doc)
    assert "# 라이브 증빙 번들" in markdown
    assert "make validate" in markdown
    json_path, md_path = write_live_evidence_bundle(doc, tmp_path)
    assert json_path.exists()
    assert md_path.exists()

    report_dir = tmp_path / "reports/runtime"
    report_dir.mkdir(parents=True)
    live_report = report_dir / "runtime_validation_20260509T000000Z.json"
    latest_report = report_dir / "runtime_validation_20260509T010000Z.json"
    live_report.write_text('{"mode":"live"}', encoding="utf-8")
    latest_report.write_text('{"mode":"live"}', encoding="utf-8")
    assert latest_runtime_validation_report(tmp_path) == latest_report
