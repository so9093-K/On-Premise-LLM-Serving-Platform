from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ai_model_serving.runtime_validation import RuntimeValidator, load_runtime_config, render_vllm_command
from ai_model_serving.runtime_validation.reporting import write_reports
from ai_model_serving.runtime_validation.results import CheckResult

ROOT = Path(__file__).resolve().parents[2]

def _clear_runtime_endpoint_env(monkeypatch) -> None:
    # Keep runtime validation unit tests hermetic even when a developer has
    # generated a local .env via bootstrap/init-env-compose. load_runtime_config
    # intentionally reads .env, and python-dotenv does not override existing
    # environment keys by default. Setting empty strings therefore prevents .env
    # values from leaking in while still allowing the config loader's `or default`
    # fallback behavior to be exercised.
    for key in [
        "GATEWAY_BASE_URL",
        "RISK_ADAPTER_BASE_URL",
        "MAIN_LLM_BASE_URL",
        "EMBEDDING_BASE_URL",
        "RISK_PROMPT_BASE_URL",
        "RISK_SIREN_BASE_URL",
        "PROMETHEUS_BASE_URL",
    ]:
        monkeypatch.setenv(key, "")


def test_runtime_validation_config_only_runner_records_expected_checks(monkeypatch) -> None:
    _clear_runtime_endpoint_env(monkeypatch)

    args = SimpleNamespace(
        root=str(ROOT),
        output_dir="reports/runtime",
        gateway_base="http://localhost:9400",
        risk_base="http://localhost:9405",
        main_llm_base="http://localhost:9401/v1",
        embedding_base="http://localhost:9402/v1",
        risk_prompt_base="http://localhost:9403/v1",
        risk_siren_base="http://localhost:9404/v1",
        prometheus_base="http://localhost:9410",
        api_key="",
        admin_api_key="",
        timeout_seconds=30,
        soak_seconds=1800,
        soak_interval_seconds=1.0,
        concurrency=1,
        skip_soak=False,
        config_only=True,
        allow_failures=False,
    )

    validator = RuntimeValidator(load_runtime_config(args))
    validator.run_config_only()

    assert len(validator.results) == 17
    assert all(item.passed for item in validator.results)
    assert {item.category for item in validator.results} >= {
        "vllm-runtime",
        "monitoring-scrape",
        "model-resource-control",
        "gpu-capacity",
        "runtime-validation-matrix",
        "model-list-schema",
        "operator-runtime-targets",
        "operator-storage-paths",
        "operator-status-bundle",
        "operator-monitoring-projection",
    }


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
        risk_siren_base=None,
        prometheus_base=None,
        api_key="",
        admin_api_key="",
        timeout_seconds=30,
        soak_seconds=1800,
        soak_interval_seconds=1.0,
        concurrency=1,
        skip_soak=False,
        config_only=True,
        allow_failures=False,
    )

    config = load_runtime_config(args)
    assert config.gateway_base == "http://cli-gateway:7777"

    args.gateway_base = None
    config = load_runtime_config(args)
    assert config.gateway_base == "http://env-gateway:9999"
    assert config.risk_base == "http://localhost:9405"


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
            "structured_outputs": {"enabled": True, "backend": "xgrammar:disable-any-whitespace", "enable_in_reasoning": True},
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
    assert json.loads(command[structured_index + 1]) == {"backend": "xgrammar:disable-any-whitespace", "enable_in_reasoning": True}


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
        mode="config-only",
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


def test_runtime_validation_uses_model_registry_projection_for_model_expectations() -> None:
    args = SimpleNamespace(
        root=str(ROOT),
        output_dir="reports/runtime",
        gateway_base="http://localhost:9400",
        risk_base="http://localhost:9405",
        main_llm_base="http://localhost:9401/v1",
        embedding_base="http://localhost:9402/v1",
        risk_prompt_base="http://localhost:9403/v1",
        risk_siren_base="http://localhost:9404/v1",
        prometheus_base="http://localhost:9410",
        api_key="",
        admin_api_key="",
        timeout_seconds=30,
        soak_seconds=1800,
        soak_interval_seconds=1.0,
        concurrency=1,
        skip_soak=False,
        config_only=True,
        allow_failures=False,
    )
    validator = RuntimeValidator(load_runtime_config(args))

    assert validator.registry.public_logical_ids() == ("local-main", "local-embed", "local-embed-ko", "risk-prompt")
    assert "risk_siren" not in validator.config.vllm_bases


def test_runtime_validation_live_mode_registers_runtime_canaries(monkeypatch) -> None:
    _clear_runtime_endpoint_env(monkeypatch)
    args = SimpleNamespace(
        root=str(ROOT),
        output_dir="reports/runtime",
        gateway_base="http://localhost:9400",
        risk_base="http://localhost:9405",
        main_llm_base="http://localhost:9401/v1",
        embedding_base="http://localhost:9402/v1",
        risk_prompt_base="http://localhost:9403/v1",
        risk_siren_base="http://localhost:9404/v1",
        prometheus_base="http://localhost:9410",
        api_key="",
        admin_api_key="",
        timeout_seconds=30,
        soak_seconds=1800,
        soak_interval_seconds=1.0,
        concurrency=1,
        skip_soak=False,
        config_only=False,
        allow_failures=False,
    )
    validator = RuntimeValidator(load_runtime_config(args))
    registered: list[tuple[str, str]] = []
    validator.safe_check = lambda category, name, fn: registered.append((category, name))  # type: ignore[method-assign]

    validator.run_live()

    categories = {category for category, _name in registered}
    expected_canaries = {
        "response-format-text-canary",
        "response-format-json-object-canary",
        "response-format-json-schema-canary",
        "logprobs-non-stream-canary",
        "logprobs-stream-canary",
        "logit-bias-shape-canary",
        "json-schema-with-tools-canary",
        "json-schema-with-reasoning-canary",
        "gemma4-reasoning-parser-structured-outputs-canary",
    }
    matrix_ids = {
        item["id"]
        for item in validator.registry.runtime_validation_matrix_document()["validation_checks"]
        if item["id"].endswith("-canary")
    }
    assert expected_canaries.issubset(categories)
    assert matrix_ids == expected_canaries


def test_operator_runtime_targets_report_is_registry_backed(tmp_path: Path) -> None:
    import yaml

    from ai_model_serving.domain import ModelRegistry
    from ai_model_serving.operator_reports import runtime_targets_document, runtime_targets_markdown, write_runtime_targets_report

    registry = ModelRegistry(
        yaml.safe_load((ROOT / "configs/model_catalog.yaml").read_text(encoding="utf-8")),
        yaml.safe_load((ROOT / "configs/model_serving.yaml").read_text(encoding="utf-8")),
    )
    document = runtime_targets_document(registry)
    assert document["compose_service_regex"] == "main-llm-vllm|embedding-vllm|embedding-ko-vllm|risk-prompt-vllm"
    assert document["runtime_targets"][0]["logical_id"] == "local-main"
    markdown = runtime_targets_markdown(document)
    assert "# 런타임 대상 인벤토리" in markdown
    assert "원문 프롬프트" in markdown

    json_path, md_path = write_runtime_targets_report(registry, tmp_path)
    assert json_path.exists()
    assert md_path.exists()


def test_operator_status_bundle_report_is_registry_backed(tmp_path: Path) -> None:
    import yaml

    from ai_model_serving.domain import ModelRegistry
    from ai_model_serving.operator_status import (
        operator_status_bundle_document,
        operator_status_bundle_markdown,
        write_operator_status_bundle,
    )

    registry = ModelRegistry(
        yaml.safe_load((ROOT / "configs/model_catalog.yaml").read_text(encoding="utf-8")),
        yaml.safe_load((ROOT / "configs/model_serving.yaml").read_text(encoding="utf-8")),
    )
    document = operator_status_bundle_document(
        registry=registry,
        monitoring=yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8")),
        gpu_budgets=yaml.safe_load((ROOT / "configs/gpu_budgets.yaml").read_text(encoding="utf-8")),
        version="0.1.0-test",
    )

    assert document["privacy_contract"] == {
        "raw_prompt_included": False,
        "user_text_included": False,
        "model_output_included": False,
        "authorization_header_included": False,
    }
    assert document["operator_commands"]["operator_status"] == "make operator-status"
    assert document["gpu_budget_summary"]["runtime_service_count"] == 4
    assert document["gpu_budget_summary"]["reserve_gib_hard_minimum"] == 3.5
    assert document["monitoring_summary"]["model_labels"] == ["local-main", "local-embed", "local-embed-ko", "risk-prompt"]
    markdown = operator_status_bundle_markdown(document)
    assert "# 운영 상태 번들" in markdown
    assert "make runtime-validate" in markdown
    assert "reserve hard minimum GiB" in markdown
    assert "원문 프롬프트" in markdown

    json_path, md_path = write_operator_status_bundle(document, tmp_path)
    assert json_path.exists()
    assert md_path.exists()


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


def test_monitoring_projection_report_is_registry_backed(tmp_path: Path) -> None:
    import yaml

    from ai_model_serving.domain import ModelRegistry
    from ai_model_serving.monitoring_projection import (
        monitoring_projection_document,
        monitoring_projection_markdown,
        prometheus_scrape_config_document,
        write_monitoring_projection_report,
    )

    registry = ModelRegistry(
        yaml.safe_load((ROOT / "configs/model_catalog.yaml").read_text(encoding="utf-8")),
        yaml.safe_load((ROOT / "configs/model_serving.yaml").read_text(encoding="utf-8")),
    )
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    projected_prometheus = prometheus_scrape_config_document(registry=registry, monitoring=monitoring)
    actual_prometheus = yaml.safe_load((ROOT / "ops/prometheus/prometheus.yml").read_text(encoding="utf-8"))
    assert projected_prometheus == actual_prometheus

    document = monitoring_projection_document(registry=registry, monitoring=monitoring)
    assert document["recording_rules"]["compose_service_regex"] == "main-llm-vllm|embedding-vllm|embedding-ko-vllm|risk-prompt-vllm"
    assert document["grafana_variables"]["model_values"] == ["local-main", "local-embed", "local-embed-ko", "risk-prompt"]
    assert document["privacy_contract"] == {
        "raw_prompt_included": False,
        "user_text_included": False,
        "model_output_included": False,
        "authorization_header_included": False,
    }
    markdown = monitoring_projection_markdown(document)
    assert "# 모니터링 Projection" in markdown
    assert "Prometheus scrape job" in markdown
    assert "원문 프롬프트" in markdown

    json_path, md_path = write_monitoring_projection_report(document, tmp_path)
    assert json_path.exists()
    assert md_path.exists()


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
    assert "make release-check" in markdown
    json_path, md_path = write_live_evidence_bundle(doc, tmp_path)
    assert json_path.exists()
    assert md_path.exists()

    report_dir = tmp_path / "reports/runtime"
    report_dir.mkdir(parents=True)
    live_report = report_dir / "runtime_validation_20260509T000000Z.json"
    config_only_report = report_dir / "runtime_validation_20260509T010000Z.json"
    live_report.write_text('{"mode":"live"}', encoding="utf-8")
    config_only_report.write_text('{"mode":"config-only"}', encoding="utf-8")
    assert latest_runtime_validation_report(tmp_path) == live_report
    assert latest_runtime_validation_report(tmp_path, prefer_live=False) == config_only_report
