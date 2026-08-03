from __future__ import annotations

from typing import Any, Callable

from ai_model_serving.domain import ModelRegistry
from ai_model_serving.monitoring_projection import monitoring_projection_document
from ai_model_serving.operator_reports import runtime_targets_document
from ai_model_serving.operator_status import operator_status_bundle_document

from .config import RuntimeValidationConfig
from .results import CheckResult
from .vllm_commands import render_vllm_command

RecordFn = Callable[[CheckResult], None]


class ConfigOnlyChecks:
    """실행 중인 서비스 없이 수행하는 runtime validation 정적·projection 검사다."""

    def __init__(self, *, config: RuntimeValidationConfig, registry: ModelRegistry, record: RecordFn) -> None:
        self.config = config
        self.model_serving = config.model_serving
        self.monitoring = config.monitoring
        self.services = config.services
        self.gpu_budgets = config.gpu_budgets
        self.registry = registry
        self.record = record

    def run(self) -> None:
        runtime_services = self.registry.iter_runtime_services()
        validation_targets = self.registry.runtime_validation_targets()
        target_service_keys = {target.service_key for target in validation_targets}
        runtime_service_keys = {service.service_key for service in runtime_services}
        matrix_ok = target_service_keys == runtime_service_keys and all(target.logical_id for target in validation_targets)
        self.record(CheckResult(
            "runtime-validation-matrix",
            "registry runtime validation targets",
            "pass" if matrix_ok else "fail",
            details={"targets": [target.as_dict() for target in validation_targets]},
        ))

        runtime_report = self._record_operator_projections(runtime_services)
        self._record_status_bundle(runtime_report)
        self._record_monitoring_projection()
        self._record_vllm_command_projection(runtime_services)
        self._record_resource_control(runtime_services)
        self._record_fixed_detector_budget()

    def _record_operator_projections(self, runtime_services: tuple[Any, ...]) -> dict[str, Any]:
        runtime_report = runtime_targets_document(self.registry)
        report_ok = (
            len(runtime_report["runtime_targets"]) == len(runtime_services)
            and runtime_report["compose_service_regex"] == self.registry.monitoring_compose_service_regex()
        )
        self.record(CheckResult(
            "operator-runtime-targets",
            "registry runtime target report projection",
            "pass" if report_ok else "fail",
            details={
                "runtime_targets": runtime_report["runtime_targets"],
                "compose_service_regex": runtime_report["compose_service_regex"],
            },
        ))

        return runtime_report

    def _record_status_bundle(self, runtime_report: dict[str, Any]) -> None:
        status_bundle = operator_status_bundle_document(
            registry=self.registry,
            monitoring=self.monitoring,
            services=self.services,
            gpu_budgets=self.gpu_budgets,
            version=self.config.version,
        )
        bundle_ok = (
            status_bundle["runtime_targets"] == runtime_report["runtime_targets"]
            and status_bundle["compose_service_regex"] == self.registry.monitoring_compose_service_regex()
            and status_bundle["privacy_contract"] == {
                "raw_prompt_included": False,
                "user_text_included": False,
                "model_output_included": False,
                "authorization_header_included": False,
            }
            and len(status_bundle["runtime_validation_matrix"]) == len(self.registry.runtime_validation_matrix_checks())
        )
        self.record(CheckResult(
            "operator-status-bundle",
            "registry operator status bundle projection",
            "pass" if bundle_ok else "fail",
            details={
                "runtime_targets": len(status_bundle["runtime_targets"]),
                "validation_checks": len(status_bundle["runtime_validation_matrix"]),
                "compose_service_regex": status_bundle["compose_service_regex"],
                "privacy_contract": status_bundle["privacy_contract"],
            },
        ))

    def _record_monitoring_projection(self) -> None:
        monitoring_projection = monitoring_projection_document(registry=self.registry, monitoring=self.monitoring, services=self.services)
        monitoring_ok = (
            monitoring_projection["recording_rules"]["compose_service_regex"] == self.registry.monitoring_compose_service_regex()
            and monitoring_projection["privacy_contract"] == {
                "raw_prompt_included": False,
                "user_text_included": False,
                "model_output_included": False,
                "authorization_header_included": False,
            }
        )
        self.record(CheckResult(
            "operator-monitoring-projection",
            "registry monitoring projection",
            "pass" if monitoring_ok else "fail",
            details={
                "scrape_jobs": [job.get("job_name") for job in monitoring_projection["prometheus_scrape_config"]["scrape_configs"]],
                "model_labels": monitoring_projection["grafana_variables"]["model_values"],
                "runtime_service_labels": monitoring_projection["grafana_variables"]["runtime_service_values"],
                "compose_service_regex": monitoring_projection["recording_rules"]["compose_service_regex"],
            },
        ))

    def _record_vllm_command_projection(self, runtime_services: tuple[Any, ...]) -> None:
        for service in runtime_services:
            command = render_vllm_command(service.service_key, service.config)
            ok = bool(command)
            self.record(CheckResult("vllm-runtime", f"render command {service.service_key}", "pass" if ok else "fail", details={"command": command}))

        required_metrics = self.monitoring["metric_sources"]["gateway"]["required_metrics"]
        self.record(CheckResult("monitoring-scrape", "monitoring config gateway required metrics", "pass" if "http_requests_total" in required_metrics else "fail", details={"required_metrics": required_metrics}))

    def _record_resource_control(self, runtime_services: tuple[Any, ...]) -> None:
        for service in runtime_services:
            control = service.config.get("resource_control", {})
            ok = bool(control.get("isolation")) and bool(control.get("request_limits")) and bool(control.get("admission_control"))
            self.record(CheckResult("model-resource-control", f"resource control {service.service_key}", "pass" if ok else "fail", details=control))

    def _record_fixed_detector_budget(self) -> None:
        fixed = self.gpu_budgets.get("resource_management", {}).get("fixed_constraints", [])
        # service_key와 GPU 예산은 vLLM detector에만 있다. local detector는 process 안에서 실행된다.
        detector_service_keys = [
            str(detector["service_key"])
            for detector in self.model_serving.get("risk_adapter", {}).get("detectors", {}).values()
            if detector.get("enabled", True) is True and detector.get("type", "vllm") == "vllm" and "service_key" in detector
        ]
        detector_tokens_ok = all(
            int(self.model_serving["models"][key].get("max_output_tokens", 0)) == 1
            for key in detector_service_keys
        )
        self.record(CheckResult(
            "model-resource-control",
            "fixed detector token budget",
            "pass" if detector_tokens_ok and any("max_output_tokens" in item for item in fixed) else "fail",
            details={"fixed_constraints": fixed},
        ))
