#!/usr/bin/env python3
"""Live runtime 상태에서 reviewable Main Model qualification candidate를 생성한다."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ai_model_serving.configuration import load_yaml_mapping  # noqa: E402
from ai_model_serving.deployment_target import load_deployment_target  # noqa: E402
from scripts.lib.process_env import load_dotenv  # noqa: E402
from scripts.lib.service_endpoint import published_base_url  # noqa: E402
from scripts.qualification.context import (  # noqa: E402
    QualificationContextError,
    qualification_context_for_runtime,
)
from scripts.qualification.hardware import (  # noqa: E402
    GpuObservation,
    QualificationHardwareError,
    observe_nvidia_gpu,
)
from scripts.validation.governance.qualification import (  # noqa: E402
    load_qualification_check_contract,
)

_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUNTIME_STATUS_MAP = {"pass": "passed", "fail": "failed", "skip": "skipped"}
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_RECEIPT_KIND = "qualification_run_receipt"


class QualificationCandidateError(RuntimeError):
    """Candidate를 정직하게 만들 수 없을 때 발생한다."""


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualificationCandidateError(f"{label} must be a mapping")
    return value


def _iso_utc(value: object, label: str) -> datetime:
    if not _non_empty(value):
        raise QualificationCandidateError(
            f"{label} must be a timezone-aware ISO timestamp"
        )
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError as exc:
        raise QualificationCandidateError(
            f"{label} must be a timezone-aware ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise QualificationCandidateError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def fetch_main_model_status(
    gateway_base: str,
    *,
    admin_api_key: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if admin_api_key:
        headers["Authorization"] = f"Bearer {admin_api_key}"
    request = urllib.request.Request(
        gateway_base.rstrip("/") + "/admin/main-model",
        method="GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports HTTP/JSON failure uniformly.
        raise QualificationCandidateError(
            f"failed to read current Main Model status from {request.full_url}: {exc}"
        ) from exc
    return _mapping(payload, "Main Model status")


def load_runtime_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationCandidateError(
            f"runtime report must be readable JSON: {path}"
        ) from exc
    return _mapping(payload, "runtime report")


def _collect_check_results(
    report: dict[str, Any],
    *,
    known_checks: set[str],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    results = report.get("results")
    if not isinstance(results, list):
        raise QualificationCandidateError("runtime report.results must be a list")

    statuses: dict[str, str] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(results):
        if not isinstance(raw, dict):
            raise QualificationCandidateError(
                f"runtime report.results[{index}] must be a mapping"
            )
        check_id = str(raw.get("qualification_check_id") or "").strip()
        if not check_id:
            continue
        if check_id not in known_checks:
            raise QualificationCandidateError(
                f"runtime report contains unknown qualification check {check_id!r}"
            )
        if check_id in statuses:
            raise QualificationCandidateError(
                f"runtime report contains duplicate qualification check {check_id!r}"
            )
        status = _RUNTIME_STATUS_MAP.get(str(raw.get("status") or ""))
        if status is None:
            raise QualificationCandidateError(
                f"runtime report qualification check {check_id!r} has unsupported "
                f"status {raw.get('status')!r}"
            )
        statuses[check_id] = status
        raw_by_id[check_id] = raw
    return statuses, raw_by_id


def build_candidate_receipt(
    *,
    report: dict[str, Any],
    main_model_status: dict[str, Any],
    deployment_target: str,
    known_checks: set[str],
    capability_requirements: dict[str, frozenset[str]],
    gpu: GpuObservation,
) -> dict[str, Any]:
    if report.get("mode") != "live":
        raise QualificationCandidateError("runtime report.mode must be 'live'")
    validated_at = _iso_utc(report.get("finished_at"), "runtime report.finished_at")
    report_started = _iso_utc(report.get("started_at"), "runtime report.started_at")
    if report_started > validated_at:
        raise QualificationCandidateError(
            "runtime report.started_at must not be after finished_at"
        )

    raw_context = _mapping(
        report.get("qualification_context"),
        "runtime report.qualification_context",
    )
    if raw_context.get("stable") is not True:
        raise QualificationCandidateError(
            "runtime report qualification context must be stable"
        )
    started_context = _mapping(
        raw_context.get("started"),
        "runtime report.qualification_context.started",
    )
    finished_context = _mapping(
        raw_context.get("finished"),
        "runtime report.qualification_context.finished",
    )
    if started_context != finished_context:
        raise QualificationCandidateError(
            "runtime report qualification context changed during validation"
        )
    try:
        current_context = qualification_context_for_runtime(
            main_model_status,
            deployment_target=deployment_target,
            hardware=gpu.as_context(),
        )
    except QualificationContextError as exc:
        raise QualificationCandidateError(str(exc)) from exc
    if finished_context != current_context:
        raise QualificationCandidateError(
            "runtime report qualification context does not match current Main Model"
        )

    active = _mapping(main_model_status.get("active_profile"), "active_profile")
    observed = _mapping(main_model_status.get("observed_runtime"), "observed_runtime")
    profile_id = str(active.get("id") or "").strip()
    model_id = str(active.get("upstream_model_id") or "").strip()
    revision = str(active.get("revision") or "").strip()
    if not profile_id or not model_id or not revision:
        raise QualificationCandidateError(
            "active_profile must expose id, upstream_model_id, and revision"
        )
    if main_model_status.get("gate") != "open" or main_model_status.get("runtime_state") != "active":
        raise QualificationCandidateError(
            "Main Model must have gate=open and runtime_state=active"
        )
    if observed.get("status") != "ready" or observed.get("health") != "healthy":
        raise QualificationCandidateError(
            "observed Main Model runtime must be ready and healthy"
        )
    if observed.get("profile_id") != profile_id:
        raise QualificationCandidateError(
            "current observed runtime profile does not match active_profile"
        )

    active_image = str(active.get("runtime_image") or "").strip()
    image_ref = str(observed.get("image_ref") or "").strip()
    if active_image and image_ref and active_image != image_ref:
        raise QualificationCandidateError(
            "current observed runtime image_ref does not match active profile runtime_image"
        )
    image_digest = str(observed.get("image_digest") or "").strip()
    if not _IMAGE_DIGEST.fullmatch(image_digest):
        raise QualificationCandidateError(
            "current observed runtime requires a registry/distribution image_digest"
        )

    engine = _mapping(observed.get("runtime_engine"), "observed_runtime.runtime_engine")
    engine_name = str(engine.get("name") or "").strip()
    engine_version = str(engine.get("version") or "").strip()
    if not engine_name or not engine_version:
        raise QualificationCandidateError(
            "current observed runtime requires engine name and version"
        )

    raw_capabilities = _mapping(active.get("capabilities"), "active_profile.capabilities").get(
        "deployed_input"
    )
    if (
        not isinstance(raw_capabilities, list)
        or not raw_capabilities
        or not all(_non_empty(item) for item in raw_capabilities)
        or len(set(raw_capabilities)) != len(raw_capabilities)
    ):
        raise QualificationCandidateError(
            "active_profile.capabilities.deployed_input must be a unique non-empty list"
        )
    capabilities = [str(item) for item in raw_capabilities]

    statuses, raw_by_id = _collect_check_results(report, known_checks=known_checks)
    required: set[str] = set()
    for capability in capabilities:
        requirement = capability_requirements.get(capability)
        if requirement is None:
            raise QualificationCandidateError(
                f"capability {capability!r} has no required qualification check mapping"
            )
        required.update(requirement)
    missing = sorted(required - statuses.keys())
    if missing:
        raise QualificationCandidateError(
            "runtime report is missing required qualification checks: " + ", ".join(missing)
        )

    gateway_models = raw_by_id.get("main_model.gateway.models")
    if gateway_models is not None:
        details = gateway_models.get("details")
        details = details if isinstance(details, dict) else {}
        raw_modalities = details.get("main_model_input_modalities")
        if not isinstance(raw_modalities, list):
            raise QualificationCandidateError(
                "main_model.gateway.models must record main_model_input_modalities"
            )
        report_modalities = {str(item) for item in raw_modalities if isinstance(item, str)}
        if report_modalities != set(capabilities):
            raise QualificationCandidateError(
                "runtime report Main Model modalities do not match current active profile"
            )

    checks = [
        {"id": check_id, "status": statuses[check_id]}
        for check_id in sorted(statuses)
    ]
    result = "passed" if all(item["status"] == "passed" for item in checks) else "failed"

    observations: dict[str, Any] = {
        "runtime_report_started_at": report_started.isoformat(),
        "runtime_image_ref": image_ref or None,
        "runtime_image_id": observed.get("image_id"),
        "gpu_uuid": gpu.uuid,
        "gpu_memory_total_mib": gpu.memory_total_mib,
    }
    observations = {key: value for key, value in observations.items() if value not in (None, "")}

    record: dict[str, Any] = {
        "kind": "qualified_run",
        "subject": {
            "type": "main_model_profile",
            "profile_id": profile_id,
            "model_id": model_id,
            "revision": revision,
        },
        "deployment_target": deployment_target,
        "capabilities": capabilities,
        "result": result,
        "validated_at": validated_at.isoformat(),
        "runtime": {
            "engine": engine_name,
            "version": engine_version,
            "image_digest": image_digest,
        },
        "checks": checks,
        "hardware": {
            "gpu": gpu.name,
            "driver_version": gpu.driver_version,
        },
        "observations": observations,
    }
    return {"version": 1, "kind": _RECEIPT_KIND, "record": record}


def candidate_record_id(receipt: dict[str, Any]) -> str:
    record = _mapping(receipt.get("record"), "candidate record")
    subject = _mapping(record.get("subject"), "candidate subject")
    runtime = _mapping(record.get("runtime"), "candidate runtime")
    validated_at = _iso_utc(record.get("validated_at"), "candidate validated_at")
    profile_id = _SAFE_ID.sub("-", str(subject.get("profile_id") or "")).strip("-")
    digest = str(runtime.get("image_digest") or "").removeprefix("sha256:")
    if not profile_id or len(digest) < 12:
        raise QualificationCandidateError("candidate identity is incomplete")
    return (
        f"{validated_at.strftime('%Y%m%dT%H%M%SZ')}-"
        f"{profile_id}-{digest[:12]}"
    )


def write_candidate(
    receipt: dict[str, Any],
    *,
    root: Path,
    output_dir: str,
) -> Path:
    requested = Path(output_dir)
    destination_dir = requested if requested.is_absolute() else root / requested
    destination_dir = destination_dir.resolve()
    try:
        destination_dir.relative_to(root)
    except ValueError:
        pass
    else:
        candidate_root = (root / "reports/qualification").resolve()
        try:
            destination_dir.relative_to(candidate_root)
        except ValueError as exc:
            raise QualificationCandidateError(
                "candidate output inside the repository must stay under "
                "reports/qualification; evidence promotion is a separate reviewed step"
            ) from exc

    destination_dir.mkdir(parents=True, exist_ok=True)
    path = destination_dir / f"{candidate_record_id(receipt)}.json"
    serialized = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != serialized:
            raise QualificationCandidateError(
                f"candidate path already exists with different content: {path}"
            )
        return path
    path.write_text(serialized, encoding="utf-8")
    return path


def _first_csv_value(value: str) -> str:
    values = [item.strip() for item in value.split(",") if item.strip()]
    return values[0] if values else ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="runtime-validation report와 현재 runtime/hardware 관측으로 qualification candidate를 생성합니다."
    )
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--runtime-report", required=True)
    parser.add_argument("--output-dir", default="reports/qualification")
    parser.add_argument("--gateway-base", default=None)
    parser.add_argument("--admin-api-key", default="")
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--nvidia-smi-bin", default="nvidia-smi")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    try:
        load_dotenv(root)
        target_id = os.getenv("DEPLOYMENT_TARGET", "").strip()
        if not target_id:
            raise QualificationCandidateError(
                "DEPLOYMENT_TARGET is missing; run make setup for the host first"
            )
        target = load_deployment_target(root / "configs/deployment_targets.yaml", target_id)
        if target.platform != "linux" or target.runtime_backend != "vllm-cuda":
            raise QualificationCandidateError(
                "qualification producer v1 supports Linux/NVIDIA vLLM targets only"
            )

        services = load_yaml_mapping(root / "configs/services.yaml").get("services")
        if not isinstance(services, dict):
            raise QualificationCandidateError("configs/services.yaml services mapping is missing")
        gateway_base = (
            str(args.gateway_base).strip()
            if args.gateway_base
            else os.getenv("RUNTIME_VALIDATION_GATEWAY_BASE_URL", "").strip()
        ) or published_base_url(services, "gateway")
        admin_api_key = (
            args.admin_api_key
            or os.getenv("ADMIN_API_KEY", "")
            or _first_csv_value(os.getenv("ADMIN_API_KEYS", ""))
        )

        report = load_runtime_report(Path(args.runtime_report).resolve())
        status = fetch_main_model_status(
            gateway_base,
            admin_api_key=admin_api_key,
            timeout_seconds=args.timeout_seconds,
        )
        try:
            gpu = observe_nvidia_gpu(args.nvidia_smi_bin)
        except QualificationHardwareError as exc:
            raise QualificationCandidateError(str(exc)) from exc
        try:
            known_checks, capability_requirements = load_qualification_check_contract(
                load_yaml_mapping(root / "configs/qualification_checks.yaml")
            )
        except SystemExit as exc:
            raise QualificationCandidateError(str(exc)) from exc

        receipt = build_candidate_receipt(
            report=report,
            main_model_status=status,
            deployment_target=target.target_id,
            known_checks=known_checks,
            capability_requirements=capability_requirements,
            gpu=gpu,
        )
        path = write_candidate(receipt, root=root, output_dir=args.output_dir)
    except QualificationCandidateError as exc:
        print(f"qualification candidate error: {exc}", file=sys.stderr)
        return 2

    print(f"wrote {path}")
    print(f"record_id={candidate_record_id(receipt)} result={receipt['record']['result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
