from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .configuration import load_yaml_mapping


KNOWN_FEATURES = frozenset(
    {
        "chat",
        "embeddings",
        "retrieval",
        "risk",
        "runtime_control",
        "model_switching",
        "gpu_admission",
    }
)
_CONTROL_MODES = frozenset({"sidecar", "static"})
_LIFECYCLE_OWNERS = frozenset({"platform", "external"})
_VALIDATION_STATUSES = frozenset({"verified", "implemented", "planned", "unvalidated"})


@dataclass(frozen=True)
class DeploymentTarget:
    target_id: str
    display_name: str
    platform: str
    runtime_backend: str
    main_profile_catalog: str
    control_mode: str
    lifecycle_owner: str
    internal_service_token_required: bool
    validation_status: str
    features: frozenset[str]
    compose_files: tuple[str, ...]
    exposure_profile_applies: bool
    gateway_runtime_host: str | None

    def supports(self, feature: str) -> bool:
        return feature in self.features

    @property
    def controllable(self) -> bool:
        return self.control_mode == "sidecar" and self.lifecycle_owner == "platform"


def effective_published_compose_services(
    target: DeploymentTarget, project_root: Path
) -> set[str] | None:
    """Compose service 이름 기준으로 이 target이 실제 host에 공개하는 집합을 반환한다.

    `exposure_profile_applies=True`인 target은 진입점이 EXPOSURE_MODE에 맞는 override를
    적용하므로 exposure profile이 그대로 기준이다. 이 경우 None을 반환해 호출자가
    profile을 쓰게 한다.

    static target은 override를 적용하지 않으므로 Compose 파일의 `ports:` 선언이
    유일한 사실이다. 이 구분이 없으면 노출 진단이 그 target에 존재하지도 않는
    서비스를 공개된 것으로 보고한다.
    """
    if target.exposure_profile_applies:
        return None
    published: set[str] = set()
    for relative in target.compose_files:
        path = project_root / relative
        if not path.is_file():
            continue
        services = load_yaml_mapping(path).get("services")
        if not isinstance(services, dict):
            continue
        for name, service in services.items():
            if isinstance(service, dict) and service.get("ports"):
                published.add(str(name))
    return published


def load_deployment_target(path: Path, target_id: str | None = None) -> DeploymentTarget:
    document = load_yaml_mapping(path)
    selected = target_id or str(document.get("default_target", ""))
    targets = document.get("targets")
    if not isinstance(targets, dict) or not selected:
        raise RuntimeError(f"deployment target catalog is invalid: {path}")
    raw = targets.get(selected)
    if not isinstance(raw, dict):
        allowed = ", ".join(sorted(str(item) for item in targets))
        raise RuntimeError(f"unknown DEPLOYMENT_TARGET {selected!r}; allowed: {allowed}")

    control_mode = str(raw.get("control_mode", ""))
    lifecycle_owner = str(raw.get("lifecycle_owner", ""))
    internal_service_token_required = raw.get("internal_service_token_required")
    validation_status = str(raw.get("validation_status", ""))
    raw_features: Any = raw.get("features")
    if control_mode not in _CONTROL_MODES:
        raise RuntimeError(f"deployment target {selected!r} has invalid control_mode {control_mode!r}")
    if lifecycle_owner not in _LIFECYCLE_OWNERS:
        raise RuntimeError(
            f"deployment target {selected!r} has invalid lifecycle_owner {lifecycle_owner!r}"
        )
    if not isinstance(internal_service_token_required, bool):
        raise RuntimeError(
            f"deployment target {selected!r} internal_service_token_required must be boolean"
        )
    if validation_status not in _VALIDATION_STATUSES:
        raise RuntimeError(
            f"deployment target {selected!r} has invalid validation_status {validation_status!r}"
        )
    if not isinstance(raw_features, dict):
        raise RuntimeError(f"deployment target {selected!r} features must be a mapping")
    unknown_features = set(raw_features) - KNOWN_FEATURES
    if unknown_features:
        raise RuntimeError(
            f"deployment target {selected!r} has unknown features: {', '.join(sorted(unknown_features))}"
        )
    non_boolean_features = [
        str(key) for key, enabled in raw_features.items() if not isinstance(enabled, bool)
    ]
    if non_boolean_features:
        raise RuntimeError(
            f"deployment target {selected!r} has non-boolean features: "
            f"{', '.join(sorted(non_boolean_features))}"
        )
    platform = str(raw.get("platform", "")).strip()
    runtime_backend = str(raw.get("runtime_backend", "")).strip()
    main_profile_catalog = str(raw.get("main_profile_catalog", "")).strip()
    if not platform or not runtime_backend or not main_profile_catalog:
        raise RuntimeError(
            f"deployment target {selected!r} requires platform, runtime_backend, "
            "and main_profile_catalog"
        )
    catalog_path = Path(main_profile_catalog)
    if catalog_path.is_absolute() or ".." in catalog_path.parts:
        raise RuntimeError(
            f"deployment target {selected!r} main_profile_catalog must be a safe relative path"
        )
    # compose_files는 static 진입점이 그대로 사용하는 목록이며 노출 진단도 같은 값을
    # 읽는다. dynamic 진입점은 override를 실행 시점에 조합하므로 이 값을 쓰지 않는다.
    # 읽는 곳이 없는 선언을 남기지 않도록 static target에서만 요구한다.
    raw_compose_files = raw.get("compose_files")
    if control_mode == "static":
        if not isinstance(raw_compose_files, list) or not raw_compose_files:
            raise RuntimeError(
                f"static deployment target {selected!r} requires a non-empty compose_files list"
            )
    elif raw_compose_files is not None:
        raise RuntimeError(
            f"deployment target {selected!r} must not declare compose_files; "
            "only static targets consume it"
        )
    compose_files: list[str] = []
    for entry in raw_compose_files or []:
        if not isinstance(entry, str) or not entry.strip():
            raise RuntimeError(f"deployment target {selected!r} compose_files entries must be paths")
        entry_path = Path(entry.strip())
        if entry_path.is_absolute() or ".." in entry_path.parts:
            raise RuntimeError(
                f"deployment target {selected!r} compose_files entries must be safe relative paths"
            )
        compose_files.append(entry.strip())
    exposure_profile_applies = raw.get("exposure_profile_applies")
    if not isinstance(exposure_profile_applies, bool):
        raise RuntimeError(
            f"deployment target {selected!r} exposure_profile_applies must be boolean"
        )
    # static 진입점은 exposure override를 적용하지 않는다. 공개 집합을 profile에서
    # 읽으면 존재하지 않는 서비스를 공개된 것으로 보고하게 되므로 조합을 막는다.
    if control_mode == "static" and exposure_profile_applies:
        raise RuntimeError(
            f"static deployment target {selected!r} cannot set exposure_profile_applies=true"
        )

    gateway_runtime_host_raw = raw.get("gateway_runtime_host")
    gateway_runtime_host = None
    if gateway_runtime_host_raw is not None:
        gateway_runtime_host = str(gateway_runtime_host_raw).strip()
        if not gateway_runtime_host or "://" in gateway_runtime_host or "/" in gateway_runtime_host:
            raise RuntimeError(
                f"deployment target {selected!r} gateway_runtime_host must be a host name"
            )
        if control_mode != "static":
            raise RuntimeError(
                f"deployment target {selected!r} gateway_runtime_host is only valid for static targets"
            )

    expected_owner = "platform" if control_mode == "sidecar" else "external"
    if lifecycle_owner != expected_owner:
        raise RuntimeError(
            f"deployment target {selected!r} control_mode={control_mode!r} requires "
            f"lifecycle_owner={expected_owner!r}"
        )
    features = frozenset(str(key) for key, enabled in raw_features.items() if enabled is True)
    if "chat" not in features:
        raise RuntimeError(f"deployment target {selected!r} must enable chat")
    if "retrieval" in features and "embeddings" not in features:
        raise RuntimeError(f"deployment target {selected!r}: retrieval requires embeddings")
    lifecycle_flags = [
        feature in features
        for feature in ("runtime_control", "model_switching", "gpu_admission")
    ]
    if len(set(lifecycle_flags)) != 1:
        raise RuntimeError(
            f"deployment target {selected!r} must enable or disable runtime_control, "
            "model_switching, and gpu_admission together"
        )
    if control_mode == "static" and ({"runtime_control", "model_switching", "gpu_admission"} & features):
        raise RuntimeError(f"static deployment target {selected!r} cannot enable lifecycle control features")

    return DeploymentTarget(
        target_id=selected,
        display_name=str(raw.get("display_name", selected)),
        platform=platform,
        runtime_backend=runtime_backend,
        main_profile_catalog=main_profile_catalog,
        control_mode=control_mode,
        lifecycle_owner=lifecycle_owner,
        internal_service_token_required=internal_service_token_required,
        validation_status=validation_status,
        features=features,
        compose_files=tuple(compose_files),
        exposure_profile_applies=exposure_profile_applies,
        gateway_runtime_host=gateway_runtime_host,
    )
