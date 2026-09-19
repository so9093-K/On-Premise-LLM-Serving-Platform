from __future__ import annotations

from typing import Any


class QualificationContextError(ValueError):
    """Main Model status에서 qualification identity를 만들 수 없을 때 발생한다."""


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualificationContextError(f"{label} must be a mapping")
    return value


def _text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise QualificationContextError(f"{label} must be non-empty")
    return text


def qualification_context_from_main_model_status(
    status: dict[str, Any],
) -> dict[str, Any]:
    """Transient timestamp를 제외한 qualification identity snapshot을 만든다."""
    active = _mapping(status.get("active_profile"), "active_profile")
    observed = _mapping(status.get("observed_runtime"), "observed_runtime")
    capabilities = _mapping(active.get("capabilities"), "active_profile.capabilities")
    deployed = capabilities.get("deployed_input")
    if (
        not isinstance(deployed, list)
        or not deployed
        or not all(isinstance(item, str) and item.strip() for item in deployed)
    ):
        raise QualificationContextError(
            "active_profile.capabilities.deployed_input must be a non-empty string list"
        )
    engine = _mapping(observed.get("runtime_engine"), "observed_runtime.runtime_engine")
    raw_last_operation = status.get("last_operation")
    if raw_last_operation is None:
        last_operation_id = None
    elif isinstance(raw_last_operation, dict):
        last_operation_id = _text(raw_last_operation.get("id"), "last_operation.id")
    else:
        raise QualificationContextError("last_operation must be a mapping or null")

    return {
        "public_model": _text(status.get("public_model"), "public_model"),
        "profile_id": _text(active.get("id"), "active_profile.id"),
        "model_id": _text(active.get("upstream_model_id"), "active_profile.upstream_model_id"),
        "revision": _text(active.get("revision"), "active_profile.revision"),
        "capabilities": sorted(str(item).strip() for item in deployed),
        "runtime_image": _text(active.get("runtime_image"), "active_profile.runtime_image"),
        "gate": _text(status.get("gate"), "gate"),
        "runtime_state": _text(status.get("runtime_state"), "runtime_state"),
        "last_operation_id": last_operation_id,
        "observed_status": _text(observed.get("status"), "observed_runtime.status"),
        "observed_health": _text(observed.get("health"), "observed_runtime.health"),
        "observed_profile_id": _text(
            observed.get("profile_id"), "observed_runtime.profile_id"
        ),
        "image_ref": _text(observed.get("image_ref"), "observed_runtime.image_ref"),
        "image_id": _text(observed.get("image_id"), "observed_runtime.image_id"),
        "image_digest": _text(
            observed.get("image_digest"), "observed_runtime.image_digest"
        ),
        "runtime_engine": {
            "name": _text(engine.get("name"), "observed_runtime.runtime_engine.name"),
            "version": _text(
                engine.get("version"), "observed_runtime.runtime_engine.version"
            ),
        },
    }


def qualification_context_for_runtime(
    status: dict[str, Any],
    *,
    deployment_target: str,
    hardware: dict[str, object],
) -> dict[str, Any]:
    context = qualification_context_from_main_model_status(status)
    target = str(deployment_target or "").strip()
    if not target:
        raise QualificationContextError("deployment_target must be non-empty")
    required_hardware = ("gpu", "gpu_uuid", "gpu_memory_total_mib", "driver_version")
    missing = [key for key in required_hardware if hardware.get(key) in (None, "")]
    if missing:
        raise QualificationContextError(
            "qualification hardware context is incomplete: " + ", ".join(missing)
        )
    context["deployment_target"] = target
    context["hardware"] = {
        "gpu": str(hardware["gpu"]),
        "gpu_uuid": str(hardware["gpu_uuid"]),
        "gpu_memory_total_mib": int(hardware["gpu_memory_total_mib"]),
        "driver_version": str(hardware["driver_version"]),
    }
    return context
