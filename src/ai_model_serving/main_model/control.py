from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from .state import MainModelStateError, MainModelStateStore, MainModelSwitchLockError
from ..service_logging import service_logger

_logger = service_logger("main_model_control")


class SwitchOutcome(NamedTuple):
    """``request_switch`` 요청 결과다.

    reused=True means the supplied request_id already mapped to an existing
    operation, so that operation was returned and NO new switch was started.
    Callers surface this so an idempotent replay is never mistaken for a fresh
    switch.
    """

    operation_id: str
    reused: bool

import yaml

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_IMAGE_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
# 프로필 이미지는 리터럴 digest이거나 CI/deploy가 이를 resolve하는 단일 ${ENV_VAR} 참조일 수 있다 —
# compose의 `${RISK_VLLM_IMAGE}`와 동일한 방식으로, 파생 런타임(예: audio/multimodal 이미지)이
# 수동이 아니라 파이프라인에 의해 고정(pin)된다.
_IMAGE_ENV_REF_RE = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
_COMPATIBILITY = frozenset({"verified", "likely", "unverified", "incompatible", "unknown"})
# switch-time media boot canary가 실제로 아는 modality 집합이다. deployed_input은 이
# 값들로만 구성돼야 한다 -- 그래야 "선언한 modality는 반드시 canary된다"는 원칙이 오타나
# 미지원 값(예: "imgae") 앞에서도 깨지지 않는다.
_ALLOWED_MODALITIES = frozenset({"text", "image", "audio", "video"})
# capabilities에 이 키만 허용한다. audio_enabled/video_enabled는 deployed_input과 중복되는
# legacy 정보라 제거됐다 -- 다시 들어오면 두 소스가 어긋날 수 있으므로 설정 오류로 막는다.
_ALLOWED_CAPABILITY_KEYS = frozenset({"deployed_input"})
_TERMINAL_STATES = frozenset({"completed", "failed", "rollback_failed"})
# reconcile_if_restarted()의 재시도 backoff. admin_sidecar.py의 10초 poll
# 간격을 기준 단위로 2배씩 늘리다 최대 5분에서 멈춘다 -- validate()가 계속
# 실패하는 동안(예: active_profile과 실제 컨테이너가 어긋난 채로 남는 drift)
# GPU 엔진에 매 poll tick마다 무의미한 canary 요청을 영구히 반복하지 않기
# 위함이다.
_RECONCILE_BACKOFF_BASE_SECONDS = 10.0
_RECONCILE_BACKOFF_MAX_SECONDS = 300.0
_CLIENT_REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class MainModelConfigurationError(ValueError):
    pass


class MainModelSwitchError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class MainModelProfile:
    profile_id: str
    display_name: str
    model_id: str
    revision: str
    served_model_name: str
    command: tuple[str, ...]
    compatibility: dict[str, Any]
    capabilities: dict[str, Any]
    # Gateway가 이 프로필을 실제로 서빙할 때 적용할 입력 한도·요청 파라미터·
    # vLLM 기능 계약이다. active_profile snapshot에 함께 실어 Gateway가 전환된
    # runtime과 다른 정적 정책을 쓰지 않게 한다.
    gateway_policy: dict[str, Any]
    # 이 프로필에 대해 resolve된 런타임 이미지: 프로필이 자체 이미지를 고정하는 경우
    # (예: audio 지원 런타임) 프로필 레벨 오버라이드이고, 그렇지 않으면 공유된
    # runtime.image이다. 필수 값이며(loader가 유일한 생성자이고 항상 digest로 고정된
    # 값으로 resolve하므로) 빈 이미지가 Docker 경계까지 도달하는 일은 절대 없다.
    # 따라서 런타임 capability(예: audio 디코드 라이브러리)는 활성 프로필과 함께 이동한다.
    image: str
    # 이 프로필이 예약하는 전체 GPU VRAM의 비율
    # (--gpu-memory-utilization). 공유 GPU budget / admission planner에서 사용된다.
    vram_fraction: float = 0.9

    def public_view(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "display_name": self.display_name,
            "served_model_name": self.served_model_name,
            "upstream_model_id": self.model_id,
            "revision": self.revision,
            "compatibility": self.compatibility,
            "capabilities": self.capabilities,
            "gateway_policy": self.gateway_policy,
            "runtime_image": self.image,
            "vram_fraction": self.vram_fraction,
        }


@dataclass(frozen=True)
class MainModelCatalog:
    public_model: str
    default_profile: str
    runtime: dict[str, Any]
    profiles: dict[str, MainModelProfile]


def _parse_gpu_fraction(command: list[str]) -> float:
    """프로필 명령에서 ``--gpu-memory-utilization`` 값을 추출한다(vLLM 기본값 0.9)."""
    if "--gpu-memory-utilization" in command:
        try:
            return float(command[command.index("--gpu-memory-utilization") + 1])
        except (IndexError, ValueError):
            pass
    return 0.9


# main model의 --gpu-memory-utilization에 대한 호스트별 오버라이드. catalog 값은
# 기준 호스트(reference-host)의 기본값이며, GPU가 다른 호스트는 공유 catalog를 수정하지
# 않고도 동일한 프로필이 맞도록 이 값을 설정한다. 이는 *해당 호스트*의 VRAM 대비 비율이므로,
# GPU가 작을수록 더 큰 비율을 설정하게 된다.
GPU_UTIL_OVERRIDE_ENV = "MAIN_LLM_GPU_MEMORY_UTILIZATION"


def gpu_util_override_from_mapping(mapping: dict[str, str]) -> float | None:
    """호스트별 gpu-memory-utilization override를 파싱하고 없으면 ``None``을 반환한다."""
    raw = (mapping.get(GPU_UTIL_OVERRIDE_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise MainModelConfigurationError(
            f"{GPU_UTIL_OVERRIDE_ENV} must be a number in (0, 1]"
        ) from exc
    if not 0.0 < value <= 1.0:
        raise MainModelConfigurationError(f"{GPU_UTIL_OVERRIDE_ENV} must be in (0, 1]")
    return value


def _apply_util_override(command: list[str], override: float | None) -> list[str]:
    """호스트별 override가 반영된 ``--gpu-memory-utilization`` 명령을 반환한다."""
    if override is None:
        return command
    rendered = f"{override:g}"
    if "--gpu-memory-utilization" in command:
        out = list(command)
        out[out.index("--gpu-memory-utilization") + 1] = rendered
        return out
    return [*command, "--gpu-memory-utilization", rendered]


def _resolve_profile_image(
    profile_image: object,
    shared_image: str | None,
    env: dict[str, str] | None,
    profile_id: str,
) -> str:
    """프로필 runtime image를 digest로 고정된 값으로 해석한다.

    - ``None`` inherits the shared ``runtime.image``.
    - ``"${VAR}"`` is resolved from ``env`` (set by CI/deploy, like compose's
      ``${RISK_VLLM_IMAGE}``). When ``VAR`` is unset/empty the image is not built yet,
      so the profile inherits the shared base only to keep the sidecar booting; the
      profile's declared capabilities are unchanged (it is a multimodal model, not a
      separate text-only one), and the switch-time boot canary is what proves the live
      runtime can actually serve them — a switch to a not-yet-built runtime fails the
      canary and rolls back, so the model is never half-served.
    - A literal value must be a sha256 digest.
    """
    if profile_image is None:
        if shared_image is not None:
            return shared_image
        raise MainModelConfigurationError(f"profile {profile_id} image is required")
    if isinstance(profile_image, str):
        ref = _IMAGE_ENV_REF_RE.match(profile_image)
        if ref is not None:
            # 호출자가 env를 명시하지 않은 CLI/test 유틸리티도 Compose와 같은
            # process environment를 해석한다. 명시 env={}는 의도적으로 빈 환경이다.
            environment = os.environ if env is None else env
            resolved = environment.get(ref.group(1), "").strip()
            if not resolved and shared_image is not None:
                return shared_image
            if not resolved:
                raise MainModelConfigurationError(
                    f"profile {profile_id} image env {ref.group(1)} is empty"
                )
            if _DIGEST_IMAGE_RE.fullmatch(resolved):
                return resolved
            raise MainModelConfigurationError(
                f"profile {profile_id} image env {ref.group(1)} must resolve to a sha256 digest"
            )
        if _DIGEST_IMAGE_RE.fullmatch(profile_image):
            return profile_image
    raise MainModelConfigurationError(
        f"profile {profile_id} image must be a sha256 digest or a ${{ENV}} reference"
    )


def load_main_model_catalog(
    path: Path,
    *,
    gpu_memory_utilization_override: float | None = None,
    env: dict[str, str] | None = None,
    resolve_runtime_images: bool = True,
) -> MainModelCatalog:
    """Main Model catalog를 읽는다.

    Runtime을 생성하거나 관찰하는 호출자는 기본값 그대로, 모든 image 참조가
    실제 digest로 해석된 catalog를 사용해야 한다. 반면 HF cache 준비는 model_id와
    revision만 소비하므로, ``resolve_runtime_images=False``로 image 배포 환경과
    독립적으로 metadata만 읽을 수 있다. 이 모드는 Docker/Compose 경계에 넘기면 안 된다.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise MainModelConfigurationError("main model profile schema version must be 1")
    public_model = str(raw.get("public_model", ""))
    default_profile = str(raw.get("default_profile", ""))
    runtime = raw.get("runtime")
    profiles_raw = raw.get("profiles")
    if not public_model or not isinstance(runtime, dict) or not isinstance(profiles_raw, dict):
        raise MainModelConfigurationError("public_model, runtime, and profiles are required")
    runtime_image = runtime.get("image")
    if not isinstance(runtime_image, str) or not runtime_image.strip():
        raise MainModelConfigurationError("runtime image is required")
    # 공용 runtime image도 profile override와 같은 배포 pin을 사용한다. 이전에는
    # 여기만 과거 literal digest로 남아, image를 따로 선언하지 않은 26B 전환이
    # Docker create의 "No such image"로 실패했다. cache 준비만 이 값을 소비하지
    # 않으므로, 그 경로에서는 env ref를 해석하지 않고 원문을 보존한다.
    image = (
        _resolve_profile_image(runtime_image, None, env, "runtime")
        if resolve_runtime_images
        else runtime_image
    )
    runtime = {**runtime, "image": image}

    profiles: dict[str, MainModelProfile] = {}
    for profile_id, item in profiles_raw.items():
        if not isinstance(item, dict):
            raise MainModelConfigurationError(f"profile {profile_id} must be an object")
        revision = str(item.get("revision", ""))
        if not _REVISION_RE.fullmatch(revision):
            raise MainModelConfigurationError(f"profile {profile_id} revision must be a 40-char commit")
        alias = str(item.get("served_model_name", ""))
        if alias != public_model:
            raise MainModelConfigurationError(
                f"profile {profile_id} served_model_name must remain {public_model}"
            )
        command = item.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
            raise MainModelConfigurationError(f"profile {profile_id} command must be a string list")
        if command.count("--model") != 1 or command.count("--served-model-name") != 1:
            raise MainModelConfigurationError(f"profile {profile_id} command has invalid model identity")
        model_id = str(item.get("model_id", ""))
        if command[command.index("--model") + 1] != model_id:
            raise MainModelConfigurationError(f"profile {profile_id} command model does not match")
        if command[command.index("--served-model-name") + 1] != public_model:
            raise MainModelConfigurationError(f"profile {profile_id} command alias does not match")
        if "--revision" not in command or command[command.index("--revision") + 1] != revision:
            raise MainModelConfigurationError(f"profile {profile_id} command must use its pinned revision")
        # 호스트별 gpu-memory-utilization 오버라이드가 있으면 적용하여
        # 런타임 커맨드와 파싱된 vram_fraction이 항상 서로 일치하도록 한다.
        command = _apply_util_override(command, gpu_memory_utilization_override)
        compatibility = item.get("compatibility", {})
        if compatibility.get("status") not in _COMPATIBILITY:
            raise MainModelConfigurationError(f"profile {profile_id} has invalid compatibility status")
        capabilities = item.get("capabilities", {"deployed_input": ["text"]})
        if not isinstance(capabilities, dict):
            raise MainModelConfigurationError(f"profile {profile_id} capabilities must be an object")
        unknown_capability_keys = set(capabilities) - _ALLOWED_CAPABILITY_KEYS
        if unknown_capability_keys:
            raise MainModelConfigurationError(
                f"profile {profile_id} capabilities has unsupported key(s): {sorted(unknown_capability_keys)}"
            )
        deployed_input = capabilities.get("deployed_input")
        if (
            not isinstance(deployed_input, list)
            or not deployed_input
            or not all(isinstance(value, str) for value in deployed_input)
            or "text" not in deployed_input
            or not set(deployed_input) <= _ALLOWED_MODALITIES
        ):
            raise MainModelConfigurationError(
                f"profile {profile_id} capabilities.deployed_input must be a non-empty list including "
                f"text, drawn only from {sorted(_ALLOWED_MODALITIES)}"
            )
        gateway_policy = item.get("gateway_policy", {})
        if not isinstance(gateway_policy, dict):
            raise MainModelConfigurationError(f"profile {profile_id} gateway_policy must be an object")
        # 이전 catalog 형식은 image/boot resolution만 시험하는 최소 Profile을 허용했다.
        # 실제 배포 catalog의 정책 존재는 governance validation에서 강제한다.
        if gateway_policy:
            request_limits = gateway_policy.get("request_limits")
            if not isinstance(request_limits, dict):
                raise MainModelConfigurationError(f"profile {profile_id} gateway_policy.request_limits must be an object")
            if request_limits.get("input_modalities") != deployed_input:
                raise MainModelConfigurationError(
                    f"profile {profile_id} gateway_policy.request_limits.input_modalities must match capabilities.deployed_input"
                )
            if not isinstance(gateway_policy.get("request_parameter_policy"), dict):
                raise MainModelConfigurationError(
                    f"profile {profile_id} gateway_policy.request_parameter_policy must be an object"
                )
            try:
                if int(gateway_policy.get("max_output_tokens", 0)) <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise MainModelConfigurationError(
                    f"profile {profile_id} gateway_policy.max_output_tokens must be a positive integer"
                ) from exc
        # 프로필은 자체 런타임 이미지(예: multimodal 빌드)를 리터럴 digest나 CI/deploy가
        # resolve하는 ${ENV} 참조로 고정할 수 있다; 지정하지 않으면 공유된 runtime.image를
        # 상속한다. runtime 소비 경로에서는 반드시 digest로 해석한다. cache 준비는
        # model/revision만 사용하므로 image ref를 그대로 두어 배포 env에 의존하지 않는다.
        profile_image = item.get("image")
        if profile_image is not None and (not isinstance(profile_image, str) or not profile_image.strip()):
            raise MainModelConfigurationError(f"profile {profile_id} image must be a non-empty string")
        resolved_image = (
            _resolve_profile_image(profile_image, image, env, str(profile_id))
            if resolve_runtime_images
            else (profile_image or image)
        )
        profiles[str(profile_id)] = MainModelProfile(
            profile_id=str(profile_id),
            display_name=str(item.get("display_name", profile_id)),
            model_id=model_id,
            revision=revision,
            served_model_name=alias,
            command=tuple(command),
            compatibility=dict(compatibility),
            capabilities=dict(capabilities),
            gateway_policy=dict(gateway_policy),
            image=resolved_image,
            vram_fraction=_parse_gpu_fraction(command),
        )
    if default_profile not in profiles:
        raise MainModelConfigurationError("default_profile must reference a configured profile")
    return MainModelCatalog(public_model, default_profile, dict(runtime), profiles)


def resolve_boot_profile(
    catalog: MainModelCatalog,
    *,
    configured_profile: str | None,
    locked: bool,
    persisted_profile: str | None,
) -> str:
    configured = configured_profile or catalog.default_profile
    if configured not in catalog.profiles:
        raise MainModelConfigurationError(f"unknown MAIN_LLM_BOOT_PROFILE: {configured}")
    if locked:
        return configured
    if persisted_profile:
        if persisted_profile not in catalog.profiles:
            raise MainModelConfigurationError(
                f"persisted active profile is not configured: {persisted_profile}"
            )
        return persisted_profile
    return configured


class MainModelRuntimeBackend(Protocol):
    async def observed_profile(self, catalog: MainModelCatalog) -> str | None: ...
    async def observe_runtime(self, catalog: MainModelCatalog) -> dict[str, Any]: ...
    async def prepare(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None: ...
    async def wait_for_drain(self, timeout_seconds: float) -> None: ...
    async def replace(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None: ...
    async def validate(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None: ...
    async def stop(self, catalog: MainModelCatalog) -> None: ...
    async def start(self, catalog: MainModelCatalog) -> None: ...
    async def observed_started_at(self, catalog: MainModelCatalog) -> str | None: ...


class MainModelManager:
    def __init__(
        self,
        catalog: MainModelCatalog,
        state_store: MainModelStateStore,
        backend: MainModelRuntimeBackend,
        *,
        boot_profile: str | None = None,
        profile_locked: bool = False,
        idempotency_ttl_seconds: float | None = None,
    ) -> None:
        self.catalog = catalog
        self.state_store = state_store
        self.backend = backend
        self.profile_locked = profile_locked
        if idempotency_ttl_seconds is None:
            # request_id는 재시도 안전을 위한 키일 뿐, 영구적으로 보관되는 기록이 아니다.
            # switch는 최대 startup_timeout_seconds(vLLM load + validate)까지 실행될 수
            # 있으므로, 진행 중인 재시도를 중복 제거(dedup)하려면 이 시간을 넘어서는 창(window)이
            # 필요하고, 여기에 더해 종료 직후 호출자가 재시도할 수 있도록 동일한 유예 시간을
            # 추가한다 — 그래서 startup timeout의 두 배가 된다. 이 시간을 넘어서 재사용된
            # request_id는 재전송(replay)이 아니라 새로운 의도로 간주되어, 더 이상 새로운
            # switch를 가로채지 않는다.
            startup = float(catalog.runtime.get("startup_timeout_seconds", 600))
            idempotency_ttl_seconds = 2.0 * startup
        self._idempotency_ttl = float(idempotency_ttl_seconds)
        persisted = state_store.read().get("active_profile")
        self.boot_profile = resolve_boot_profile(
            catalog,
            configured_profile=boot_profile,
            locked=profile_locked,
            persisted_profile=persisted,
        )
        configured = boot_profile or catalog.default_profile
        if not profile_locked and persisted and persisted != configured:
            # ADR-0017 부트 우선순위(lock > 마지막으로 커밋된 활성 프로파일 >
            # MAIN_LLM_BOOT_PROFILE)에 따른 의도된 동작이지, 에러가 아니다 — .env의
            # MAIN_LLM_BOOT_PROFILE을 안 바꿔도 전환은 재시작을 넘어 유지된다. 그래도
            # .env만 보고 판단하는 운영자가 헷갈리지 않도록 로그는 남긴다.
            _logger.warning(
                "main-llm-vllm boot profile diverges from MAIN_LLM_BOOT_PROFILE "
                "(configured=%s, persisted state active_profile=%s); booting the "
                "persisted profile since it takes precedence (ADR-0017 boot "
                "priority — this is expected, not an error)",
                configured,
                persisted,
            )
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        # reconcile_if_restarted()가 validate() 실패를 반복할 때(예: 2026-07-28
        # 사고처럼 active_profile과 실제 컨테이너가 어긋난 채로 남는 경우) 매
        # poll tick(10초)마다 똑같이 재시도해 GPU 엔진에 무의미한 canary 요청을
        # 영구히 반복하는 걸 막기 위한 backoff 상태. 재시작마다 초기화되는
        # in-memory 값으로 충분하다 -- 드리프트가 해소되면(성공) 바로 리셋된다.
        self._reconcile_failure_streak = 0
        self._reconcile_backoff_until: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        state = self.state_store.read()
        active_id = state.get("active_profile")
        active = self.catalog.profiles.get(active_id) if active_id else None
        return {
            "public_model": self.catalog.public_model,
            "active_profile": active.public_view() if active else None,
            "last_known_good_profile": state.get("last_known_good_profile"),
            "previous_known_good_profile": state.get("previous_known_good_profile"),
            "gate": state.get("gate", "closed"),
            "runtime_state": state.get("runtime_state", "active"),
            "profile_locked": self.profile_locked,
            "boot_profile": self.boot_profile,
            "last_operation": state.get("last_operation"),
            "stats": state.get("stats", {}),
            # 실제로 살아있는 런타임을 지원하는 이미지는 active profile의 이미지이며
            # (공유된 runtime.image를 오버라이드할 수 있다); 아직 활성 프로필이 없으면
            # 공유 이미지로 폴백한다.
            "runtime_image": active.image if active else self.catalog.runtime.get("image"),
            "state_recovery_error": state.get("state_recovery_error"),
        }

    async def observed_snapshot(self) -> dict[str, Any]:
        """제어 ledger와 Docker에서 읽은 현재 런타임 관측값을 함께 반환한다.

        ``snapshot()``의 active profile·gate·runtime_state는 마지막으로 검증·기록한
        control-plane 상태다. 관리자가 Docker를 직접 조작했거나 Docker API 자체를 읽지
        못하는 경우에도 그 사실을 숨기지 않도록, 실제 관측값은 별도 필드에 둔다.
        """
        snapshot = self.snapshot()
        observed_at = time.time()
        try:
            observation = await self.backend.observe_runtime(self.catalog)
        except Exception as exc:  # noqa: BLE001 - 상태 조회가 Docker 장애를 숨기면 안 된다
            observation = {
                "status": "unknown",
                "container_state": None,
                "health": None,
                "profile_id": None,
                "error": str(exc),
            }
        snapshot["observed_runtime"] = {**observation, "observed_at": observed_at}
        return snapshot

    def profiles(self) -> list[dict[str, Any]]:
        active = self.state_store.read().get("active_profile")
        return [
            {**profile.public_view(), "active": profile.profile_id == active}
            for profile in self.catalog.profiles.values()
        ]

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        for operation in self.state_store.read().get("operations", []):
            if operation.get("id") == operation_id:
                return operation
        return None

    async def _validate_and_record(self, profile: MainModelProfile) -> None:
        """``backend.validate()``를 실행하고 검증한 컨테이너 instance를 기록한다.

        모든 validate 호출은 이 메서드를 거쳐야 재시작 감지가 비교할 마지막 정상
        컨테이너 fingerprint를 남긴다. 기록 실패는 이미 성공한 검증을 실패로 바꾸지 않는다.
        """
        await self.backend.validate(self.catalog, profile)
        try:
            started_at = await self.backend.observed_started_at(self.catalog)
        except Exception as exc:  # noqa: BLE001 - 순수 부기(bookkeeping)이며 validate() 성공을 뒤집으면 안 된다
            _logger.warning("failed to record container fingerprint after validate(): %s", exc)
            return
        if started_at is not None:
            self.state_store.update(
                lambda s: s.update(last_validated_container_started_at=started_at)
            )

    async def reconcile_if_restarted(self) -> None:
        """Detect a main-llm-vllm restart that bypassed this controller entirely
        (e.g. an operator running `docker restart main-llm-vllm` directly instead
        of the admin API) and re-run validate() so the active Profile's health,
        model identity, and media capability remain verified. Intended to be
        polled periodically in the background.

        Does not close the gate while this verification runs. The controller
        treats a restart as an observation problem, not as a reason to add
        user-visible downtime.

        validate()가 계속 실패하면(예: active_profile이 가리키는 프로필과 실제
        컨테이너가 어긋난 채로 남는 drift -- 2026-07-28 실제 사고) 이 메서드는
        매 poll tick마다 똑같은 재검증을 무한 반복하지 않는다. 실패할 때마다
        `_RECONCILE_BACKOFF_BASE_SECONDS`에서 시작해 지수적으로 물러나며
        `_RECONCILE_BACKOFF_MAX_SECONDS`에서 멈춘다 -- drift 자체(원인)는 안
        고치지만, 고쳐질 때까지 GPU 엔진에 무의미한 canary 요청을 영구히
        퍼붓는 건 막는다. 성공하거나 drift가 해소되면 즉시 리셋된다.
        """
        async with self._lock:
            state = self.state_store.read()
            if state.get("runtime_state") == "stopped":
                return
            last_operation = state.get("last_operation")
            if last_operation and last_operation.get("status") not in _TERMINAL_STATES:
                return  # 진행 중인 전환/복구와 경합하지 않는다
            active_id = state.get("active_profile")
            if active_id not in self.catalog.profiles:
                return
            try:
                observed_started_at = await self.backend.observed_started_at(self.catalog)
            except Exception as exc:  # noqa: BLE001 - 다음 tick에 다시 시도한다
                _logger.warning("reconciliation could not observe container start time: %s", exc)
                return
            if observed_started_at is None:
                return  # 컨테이너가 없음 — initialize()/start_main()이 처리할 문제
            last_validated = state.get("last_validated_container_started_at")
            if last_validated is None:
                # 이 필드가 아직 없는 상태(신규 배포 직후)이거나 최초 관측 —
                # drift로 취급해 불필요한 재검증을 트리거하지 않고 기준선만 기록한다.
                # 잠재 리스크: _validate_and_record()의 fingerprint 기록 단계(관측
                # 호출)만 계속 실패하고 validate() 자체는 계속 성공하는 상황이 생기면,
                # 이 필드가 영원히 None으로 남아 매 tick마다 이 분기로 빠져 재검증이
                # 트리거되지 않는다. 두 실패가 겹쳐야 하는 좁은 경우이고, 위
                # observed_started_at() 호출이 같은 backend 메서드를 쓰므로 지속적인
                # 장애라면 그 호출도 함께 실패해 이 분기에 도달하기 전에 걸러지지만,
                # 간헐적 실패 패턴에서는 이 분기가 계속 반복될 수 있다.
                self.state_store.update(
                    lambda s: s.update(last_validated_container_started_at=observed_started_at)
                )
                return
            if observed_started_at == last_validated:
                self._reconcile_failure_streak = 0
                self._reconcile_backoff_until = 0.0
                return  # 컨테이너가 그대로임 — 조치 불필요
            now = asyncio.get_running_loop().time()
            if now < self._reconcile_backoff_until:
                return  # backoff 중 — 이번 tick은 건너뛴다
            _logger.warning(
                "main-llm-vllm container restarted outside admin-sidecar control "
                "(observed StartedAt=%s, last validated=%s); re-validating without closing the gate",
                observed_started_at,
                last_validated,
            )
            try:
                await self._validate_and_record(self.catalog.profiles[active_id])
            except Exception:
                self._reconcile_failure_streak += 1
                backoff = min(
                    _RECONCILE_BACKOFF_BASE_SECONDS * (2 ** self._reconcile_failure_streak),
                    _RECONCILE_BACKOFF_MAX_SECONDS,
                )
                self._reconcile_backoff_until = now + backoff
                _logger.warning(
                    "main-llm-vllm reconcile validate() failed (streak=%d); backing off %.0fs "
                    "before the next attempt instead of retrying every poll tick",
                    self._reconcile_failure_streak,
                    backoff,
                )
                raise
            else:
                self._reconcile_failure_streak = 0
                self._reconcile_backoff_until = 0.0

    async def stop_main(self) -> None:
        """VRAM 회수를 위해 main runtime을 drain 후 중지한다.

        gate를 닫아 chat 요청은 503으로 fail-closed 처리하며, 재시작 후 자동 기동하지 않도록
        저장된 ``runtime_state``를 ``stopped``로 바꾼다.
        """
        async with self._lock:
            self.state_store.update(lambda s: s.update(gate="closed"))
            try:
                await self.backend.wait_for_drain(
                    float(self.catalog.runtime.get("drain_timeout_seconds", 30))
                )
            except Exception:  # noqa: BLE001 - drain이 타임아웃되어도 의도적으로 stop을 진행한다
                pass
            await self.backend.stop(self.catalog)
            self.state_store.update(lambda s: s.update(runtime_state="stopped"))

    async def start_main(self) -> None:
        """중지된 main runtime을 저장된 프로필로 시작하고 검증한다.

        공유 GPU 예산 admission은 호출자가 먼저 완료해야 한다.
        """
        async with self._lock:
            await self.backend.start(self.catalog)
            active_id = self.state_store.read().get("active_profile") or self.boot_profile
            profile = self.catalog.profiles[active_id]
            observed = await self.backend.observed_profile(self.catalog)
            if observed != active_id:
                await self.backend.replace(self.catalog, profile)
            await self._validate_and_record(profile)

            def commit(state: dict[str, Any]) -> None:
                state["runtime_state"] = "active"
                state["gate"] = "open"

            self.state_store.update(commit)

    async def initialize(self) -> None:
        # self._lock을 잡는다 — 안 그러면 이 부팅 시퀀스(자체적으로 validate()를 여러
        # 번 호출할 수 있어 수십 초가 걸릴 수 있다)가 진행되는 동안 reconcile_if_restarted()의
        # 첫 tick이 같은 컨테이너에 대해 동시에 또 다른 validate()를 돌릴 수 있었다
        # (reconcile은 락을 잡지만 initialize()는 원래 안 잡고 있었다). request_switch()는
        # 동기적으로 background task만 스케줄하고 반환하므로(락은 그 task 안에서 잡는다),
        # 여기서 호출해도 데드락은 없다.
        async with self._lock:
            if self.state_store.read().get("runtime_state") == "stopped":
                # 재시작 간에도 운영자가 의도적으로 내린 stop을 존중한다: 다시 끌어올려
                # reconcile하는 대신 main 런타임을 내려간 상태로, gate를 닫힌 상태로 둔다.
                self.state_store.update(lambda s: s.update(gate="closed"))
                return
            observed = await self.backend.observed_profile(self.catalog)
            interrupted = self.state_store.read().get("last_operation")
            if interrupted and interrupted.get("status") not in _TERMINAL_STATES:
                await self._recover_interrupted(interrupted, observed)
                return
            target = self.boot_profile
            if observed == target:
                await self._validate_and_record(self.catalog.profiles[target])
                self._commit_boot(target)
                return
            # compose 시작 과정에서는 먼저 정적으로 선언된 baseline 컨테이너가 healthy
            # 상태가 되도록 둔다. Compose가 아직 해당 컨테이너를 기다리는 동안 교체하면
            # dependency orchestration이 무효화될 수 있다. baseline이 관측 가능해지면
            # gate를 닫고 백그라운드에서 reconcile한다; Gateway는 persisted target이
            # validate될 때까지 fail closed 상태를 유지한다.
            if observed in self.catalog.profiles:
                await self._validate_and_record(self.catalog.profiles[observed])
            self.state_store.update(lambda state: state.update(gate="closed"))
            self.request_switch(target, confirm_unverified=True, boot_reconcile=True)

    async def _recover_interrupted(
        self, operation: dict[str, Any], observed: str | None
    ) -> None:
        operation_id = str(operation["id"])
        requested = operation.get("requested_profile")
        previous = operation.get("previous_profile")
        if observed == requested and requested in self.catalog.profiles:
            await self._validate_and_record(self.catalog.profiles[requested])
            def commit(state: dict[str, Any]) -> None:
                if previous and previous != requested:
                    state["previous_known_good_profile"] = previous
                state["active_profile"] = requested
                state["last_known_good_profile"] = requested
                state["gate"] = "open"
            self.state_store.update(commit)
            self._set_operation(operation_id, "completed", recovered_after_restart=True)
            self._record_terminal(operation_id, success=True)
            return
        if observed == previous and previous in self.catalog.profiles:
            await self._validate_and_record(self.catalog.profiles[previous])
            def rollback_commit(state: dict[str, Any]) -> None:
                state["active_profile"] = previous
                state["last_known_good_profile"] = previous
                state["gate"] = "open"
            self.state_store.update(rollback_commit)
            self._set_operation(
                operation_id,
                "failed",
                error="interrupted switch recovered on previous profile",
                recovered_after_restart=True,
            )
            self._record_terminal(operation_id, success=False, rollback=True)
            return
        self._set_operation(
            operation_id,
            "rollback_failed",
            error="interrupted switch could not be reconciled with the observed runtime",
            recovered_after_restart=True,
        )
        self.state_store.update(lambda state: state.update(gate="closed"))
        self._record_terminal(
            operation_id, success=False, rollback=True, rollback_failed=True
        )

    def _commit_boot(self, profile_id: str) -> None:
        def mutate(state: dict[str, Any]) -> None:
            state["active_profile"] = profile_id
            state["last_known_good_profile"] = profile_id
            state["gate"] = "open"
        self.state_store.update(mutate)

    def _idempotent_prior(
        self,
        operations: list[dict[str, Any]],
        client_request_id: str,
        profile_id: str,
        now: float,
    ) -> dict[str, Any] | None:
        """request_id가 idempotent하게 가리키는 기존 작업이 있으면 반환한다.

        request_id는 영구 ledger가 아닌 유효 기간이 제한된 재시도 키다. 기존 작업은 진행 중이거나
        terminal 상태가 된 뒤 ``_idempotency_ttl``초 동안에만 일치한다. 만료된 작업은 재생하거나
        충돌로 처리하지 않으므로, 유효 기간 뒤 같은 ID를 쓰면 과거 작업을 되살리지 않고 새 전환을 시작한다.
        """
        for prior in operations:
            if prior.get("client_request_id") != client_request_id:
                continue
            if prior.get("status") in _TERMINAL_STATES:
                settled_at = float(
                    prior.get("updated_at") or prior.get("created_at") or 0.0
                )
                if now - settled_at > self._idempotency_ttl:
                    continue
            if prior.get("requested_profile") != profile_id:
                raise MainModelSwitchError(
                    "SWITCH_REQUEST_ID_CONFLICT",
                    "request_id was already used for a different profile",
                    status_code=409,
                )
            return prior
        return None

    def request_switch(
        self,
        profile_id: str,
        *,
        confirm_unverified: bool = False,
        boot_reconcile: bool = False,
        client_request_id: str | None = None,
    ) -> SwitchOutcome:
        if profile_id not in self.catalog.profiles:
            raise MainModelSwitchError("MODEL_PROFILE_NOT_FOUND", f"unknown profile: {profile_id}", status_code=404)
        if self.profile_locked and not boot_reconcile:
            raise MainModelSwitchError("MODEL_PROFILE_LOCKED", "main model profile is deployment-locked")
        profile = self.catalog.profiles[profile_id]
        compatibility = profile.compatibility.get("status")
        if compatibility == "incompatible":
            raise MainModelSwitchError(
                "MODEL_PROFILE_INCOMPATIBLE",
                "the selected model is incompatible with the current deployment",
                status_code=422,
            )
        if compatibility in {"unverified", "unknown"} and not confirm_unverified:
            raise MainModelSwitchError(
                "MODEL_PROFILE_CONFIRMATION_REQUIRED",
                "the selected model is not verified; explicit confirmation is required",
                status_code=409,
            )
        if client_request_id is not None and not _CLIENT_REQUEST_RE.fullmatch(
            client_request_id
        ):
            raise MainModelSwitchError(
                "INVALID_SWITCH_REQUEST_ID",
                "request_id must be 1-128 safe identifier characters",
                status_code=422,
            )
        if client_request_id is not None:
            prior = self._idempotent_prior(
                self.state_store.read().get("operations", []),
                client_request_id,
                profile_id,
                time.time(),
            )
            if prior is not None:
                return SwitchOutcome(str(prior["id"]), True)
        operation_id = str(uuid.uuid4())
        lock_context = self.state_store.operation_lock()
        try:
            lock_context.__enter__()
        except MainModelSwitchLockError as exc:
            raise MainModelSwitchError(
                "MODEL_SWITCH_IN_PROGRESS", "another model switch process holds the lock"
            ) from exc
        # switch lock은 이 동기적인 accept 구간과 switch를 완료하는 비동기 operation
        # 구간에 걸쳐 유지되어야 하므로, 이 메서드 범위의 `with` 블록 안에 둘 수 없다.
        # 소유권은 백그라운드 task가 스케줄된 시점에 그쪽으로 넘어간다; 그 전까지는
        # 모든 종료 경로(state 업데이트 실패, idempotent reuse 반환, create_task 실패)가
        # 아래 finally를 통해 lock을 해제하므로 lock이 새어나갈(leak) 일은 없다.
        reused_operation_id: str | None = None
        operation_started = False
        try:
            def mutate(value: dict[str, Any]) -> None:
                nonlocal reused_operation_id
                if client_request_id is not None:
                    prior = self._idempotent_prior(
                        value.get("operations", []),
                        client_request_id,
                        profile_id,
                        time.time(),
                    )
                    if prior is not None:
                        reused_operation_id = str(prior["id"])
                        return
                current = value.get("last_operation")
                if current and current.get("status") not in _TERMINAL_STATES:
                    raise MainModelSwitchError(
                        "MODEL_SWITCH_IN_PROGRESS", "another model switch is in progress"
                    )
                operation = {
                    "id": operation_id,
                    "requested_profile": profile_id,
                    "previous_profile": value.get("active_profile")
                    or value.get("last_known_good_profile"),
                    "previous_gate": value.get("gate", "closed"),
                    "status": "pending",
                    "stage": "pending",
                    "error": None,
                    "rollback_error": None,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "boot_reconcile": boot_reconcile,
                    "client_request_id": client_request_id,
                }
                value["last_operation"] = operation
                value.setdefault("operations", []).append(operation)
                value["operations"] = value["operations"][-50:]
                stats = value.setdefault("stats", self._initial_stats())
                stats["switch_requests"] = int(stats.get("switch_requests", 0)) + 1
            self.state_store.update(mutate)
            if reused_operation_id is not None:
                return SwitchOutcome(reused_operation_id, True)
            task = asyncio.create_task(
                self._run(operation_id, lock_context, boot_reconcile=boot_reconcile),
                name=operation_id,
            )
            operation_started = True
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return SwitchOutcome(operation_id, False)
        finally:
            if not operation_started:
                lock_context.__exit__(None, None, None)

    def _set_operation(self, operation_id: str, status: str, **fields: Any) -> None:
        def mutate(state: dict[str, Any]) -> None:
            target = None
            for item in state.get("operations", []):
                if item.get("id") == operation_id:
                    target = item
                    break
            if target is None:
                raise MainModelStateError(f"operation disappeared: {operation_id}")
            target.update(status=status, stage=status, updated_at=time.time(), **fields)
            state["last_operation"] = target
        self.state_store.update(mutate)

    @staticmethod
    def _initial_stats() -> dict[str, int | float]:
        return {
            "switch_requests": 0,
            "switch_successes": 0,
            "switch_failures": 0,
            "rollbacks": 0,
            "rollback_failures": 0,
            "last_switch_timestamp": 0,
            "last_switch_duration_seconds": 0,
        }

    def _record_terminal(self, operation_id: str, *, success: bool, rollback: bool = False, rollback_failed: bool = False) -> None:
        operation = self.operation(operation_id) or {}
        now = time.time()
        duration = max(0.0, now - float(operation.get("created_at", now)))
        def mutate(state: dict[str, Any]) -> None:
            stats = state.setdefault("stats", self._initial_stats())
            key = "switch_successes" if success else "switch_failures"
            stats[key] = int(stats.get(key, 0)) + 1
            if rollback:
                stats["rollbacks"] = int(stats.get("rollbacks", 0)) + 1
            if rollback_failed:
                stats["rollback_failures"] = int(stats.get("rollback_failures", 0)) + 1
            stats["last_switch_timestamp"] = now
            stats["last_switch_duration_seconds"] = duration
        self.state_store.update(mutate)

    async def _run(
        self,
        operation_id: str,
        operation_lock: Any,
        *,
        boot_reconcile: bool,
    ) -> None:
        try:
            async with self._lock:
                await self._run_locked(operation_id, boot_reconcile=boot_reconcile)
        finally:
            operation_lock.__exit__(None, None, None)

    async def _run_locked(self, operation_id: str, *, boot_reconcile: bool) -> None:
        operation = self.operation(operation_id)
        if operation is None:
            return
        target = self.catalog.profiles[operation["requested_profile"]]
        previous_id = operation.get("previous_profile")
        previous_gate = str(operation.get("previous_gate", "closed"))
        entered_replace_phase = False
        try:
            self._set_operation(operation_id, "preparing")
            await self.backend.prepare(self.catalog, target)
            self.state_store.update(lambda state: state.update(gate="closed"))
            self._set_operation(operation_id, "draining")
            if not boot_reconcile:
                await self.backend.wait_for_drain(
                    float(self.catalog.runtime.get("drain_timeout_seconds", 30))
                )
            self._set_operation(operation_id, "stopping")
            # 이 지점을 지나면 이전 런타임이 해체(teardown)되는 중이므로, 이후에
            # 실패가 발생하면 이전 gate를 그냥 다시 여는 것이 아니라 반드시 rollback해야
            # 한다. replace()가 await되기 전에 설정한다: replace *도중* 실패해도
            # 이미 되돌릴 수 없는(irreversible) 단계에 진입한 것으로 본다.
            entered_replace_phase = True
            self._set_operation(operation_id, "starting")
            await self.backend.replace(self.catalog, target)
            self._set_operation(operation_id, "validating")
            await self._validate_and_record(target)
        except Exception as exc:
            if not entered_replace_phase:
                self._set_operation(operation_id, "failed", error=str(exc))
                if previous_id:
                    self.state_store.update(
                        lambda state: state.update(
                            active_profile=previous_id,
                            gate=previous_gate,
                        )
                    )
                else:
                    self.state_store.update(
                        lambda state: state.update(gate=previous_gate)
                    )
                self._record_terminal(operation_id, success=False)
                return
            self._set_operation(operation_id, "rolling_back", error=str(exc))
            if entered_replace_phase and previous_id in self.catalog.profiles:
                previous = self.catalog.profiles[previous_id]
                try:
                    await self.backend.replace(self.catalog, previous)
                    await self._validate_and_record(previous)
                except Exception as rollback_exc:
                    self._set_operation(
                        operation_id,
                        "rollback_failed",
                        error=str(exc),
                        rollback_error=str(rollback_exc),
                    )
                    self._record_terminal(
                        operation_id,
                        success=False,
                        rollback=True,
                        rollback_failed=True,
                    )
                    return
            self._set_operation(operation_id, "failed", error=str(exc))
            if previous_id:
                def reopen(state: dict[str, Any]) -> None:
                    state["active_profile"] = previous_id
                    state["gate"] = "open"
                self.state_store.update(reopen)
            self._record_terminal(
                operation_id,
                success=False,
                rollback=entered_replace_phase and previous_id in self.catalog.profiles,
            )
            return

        def commit(state: dict[str, Any]) -> None:
            prior = state.get("last_known_good_profile")
            if prior and prior != target.profile_id:
                state["previous_known_good_profile"] = prior
            state["active_profile"] = target.profile_id
            state["last_known_good_profile"] = target.profile_id
            state["gate"] = "open"
            state["runtime_state"] = "active"
        self.state_store.update(commit)
        self._set_operation(operation_id, "completed")
        self._record_terminal(operation_id, success=True)
