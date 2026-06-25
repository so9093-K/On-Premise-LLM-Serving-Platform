from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol


class SwitchOutcome(NamedTuple):
    """Result of request_switch.

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
# A profile image may be a literal digest or a single ${ENV_VAR} reference that
# CI/deploy resolves to one — mirrors compose's `${RISK_VLLM_IMAGE}`, so a derived
# runtime (e.g. the audio/multimodal image) is pinned by the pipeline, not by hand.
_IMAGE_ENV_REF_RE = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
_COMPATIBILITY = frozenset({"verified", "likely", "unverified", "incompatible", "unknown"})
_TERMINAL_STATES = frozenset({"completed", "failed", "rollback_failed"})
_CLIENT_REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class MainModelConfigurationError(ValueError):
    pass


class MainModelStateError(RuntimeError):
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
    # Resolved runtime image for this profile: a profile-level override when the
    # profile pins its own image (e.g. an audio-capable runtime), otherwise the
    # shared runtime.image. Required (the loader is the only constructor and
    # always resolves it to a digest-pinned value) so an empty image can never
    # reach the Docker boundary. The runtime capability (e.g. audio decode libs)
    # thus travels with the active profile.
    image: str
    # Fraction of total GPU VRAM this profile reserves (its
    # --gpu-memory-utilization). Used by the shared GPU budget / admission planner.
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
    """Extract --gpu-memory-utilization from a profile command (vLLM default 0.9)."""
    if "--gpu-memory-utilization" in command:
        try:
            return float(command[command.index("--gpu-memory-utilization") + 1])
        except (IndexError, ValueError):
            pass
    return 0.9


# Per-host override for the main model's --gpu-memory-utilization. The catalog
# value is the reference-host default; a host with a different GPU sets this so
# the same profiles fit without editing the shared catalog. It is a fraction of
# *that host's* VRAM, so a smaller GPU sets a larger fraction.
GPU_UTIL_OVERRIDE_ENV = "MAIN_LLM_GPU_MEMORY_UTILIZATION"


def gpu_util_override_from_mapping(mapping: dict[str, str]) -> float | None:
    """Parse the per-host gpu-memory-utilization override, or None when unset."""
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
    """Return command with --gpu-memory-utilization set to the per-host override."""
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
    shared_image: str,
    env: dict[str, str] | None,
    profile_id: str,
) -> str:
    """Resolve a profile's runtime image to a digest-pinned value.

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
        return shared_image
    if isinstance(profile_image, str):
        ref = _IMAGE_ENV_REF_RE.match(profile_image)
        if ref is not None:
            resolved = (env or {}).get(ref.group(1), "").strip()
            if not resolved:
                return shared_image
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
) -> MainModelCatalog:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise MainModelConfigurationError("main model profile schema version must be 1")
    public_model = str(raw.get("public_model", ""))
    default_profile = str(raw.get("default_profile", ""))
    runtime = raw.get("runtime")
    profiles_raw = raw.get("profiles")
    if not public_model or not isinstance(runtime, dict) or not isinstance(profiles_raw, dict):
        raise MainModelConfigurationError("public_model, runtime, and profiles are required")
    image = str(runtime.get("image", ""))
    if not _DIGEST_IMAGE_RE.fullmatch(image):
        raise MainModelConfigurationError("runtime image must be pinned by sha256 digest")

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
        # Apply the per-host gpu-memory-utilization override (if any) so the
        # runtime command and the parsed vram_fraction stay in lockstep.
        command = _apply_util_override(command, gpu_memory_utilization_override)
        compatibility = item.get("compatibility", {})
        if compatibility.get("status") not in _COMPATIBILITY:
            raise MainModelConfigurationError(f"profile {profile_id} has invalid compatibility status")
        # A profile may pin its own runtime image (e.g. a multimodal build) as a
        # literal digest or a ${ENV} reference resolved by CI/deploy; absent that it
        # inherits the shared runtime.image. Either way the resolved image is a digest.
        resolved_image = _resolve_profile_image(item.get("image"), image, env, str(profile_id))
        profiles[str(profile_id)] = MainModelProfile(
            profile_id=str(profile_id),
            display_name=str(item.get("display_name", profile_id)),
            model_id=model_id,
            revision=revision,
            served_model_name=alias,
            command=tuple(command),
            compatibility=dict(compatibility),
            capabilities=dict(item.get("capabilities", {})),
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


class MainModelStateStore:
    def __init__(self, path: Path, default_profile: str) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.operation_lock_path = path.with_suffix(path.suffix + ".operation.lock")
        self.default_profile = default_profile
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _initial(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "active_profile": None,
            "last_known_good_profile": None,
            "previous_known_good_profile": None,
            "gate": "closed",
            "runtime_state": "active",
            "last_operation": None,
            "operations": [],
            "stats": {
                "switch_requests": 0,
                "switch_successes": 0,
                "switch_failures": 0,
                "rollbacks": 0,
                "rollback_failures": 0,
                "last_switch_timestamp": 0,
                "last_switch_duration_seconds": 0,
            },
            "state_recovery_error": None,
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._initial()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except PermissionError as exc:
            raise MainModelStateError(
                f"main model state is not readable (permission denied): {self.path}"
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise MainModelStateError(f"main model state is corrupt: {self.path}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise MainModelStateError("unsupported main model state schema")
        return value

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def read(self) -> dict[str, Any]:
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._read_unlocked()

    def write(self, state: dict[str, Any]) -> None:
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self._write_unlocked(state)

    def update(self, mutate: Any) -> dict[str, Any]:
        """Atomically read, mutate, and replace state under one process lock."""
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._read_unlocked()
            mutate(state)
            self._write_unlocked(state)
            return state

    def quarantine_corrupt_state(self, reason: str) -> Path | None:
        """Move an unreadable state aside and create a fail-closed initial state."""
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            quarantined: Path | None = None
            if self.path.exists():
                quarantined = self.path.with_name(
                    f"{self.path.name}.corrupt.{int(time.time())}"
                )
                os.replace(self.path, quarantined)
            state = self._initial()
            state["state_recovery_error"] = reason
            self._write_unlocked(state)
            return quarantined

    @contextmanager
    def operation_lock(self):
        """Hold the global switch lock across the complete async operation."""
        self.operation_lock_path.touch(exist_ok=True)
        handle = self.operation_lock_path.open("r+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MainModelSwitchError(
                    "MODEL_SWITCH_IN_PROGRESS", "another model switch process holds the lock"
                ) from exc
            yield
        finally:
            handle.close()


class MainModelRuntimeBackend(Protocol):
    async def observed_profile(self, catalog: MainModelCatalog) -> str | None: ...
    async def prepare(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None: ...
    async def wait_for_drain(self, timeout_seconds: float) -> None: ...
    async def replace(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None: ...
    async def validate(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None: ...
    async def stop(self, catalog: MainModelCatalog) -> None: ...
    async def start(self, catalog: MainModelCatalog) -> None: ...
    async def is_running(self, catalog: MainModelCatalog) -> bool: ...


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
            # A request_id is a retry-safety key, not a permanent record. A switch
            # can run up to startup_timeout_seconds (vLLM load + validate), so the
            # window must outlast that for an in-flight retry to dedup, plus an equal
            # grace for a caller to retry just after it settles — hence twice the
            # startup timeout. Beyond this a re-used request_id is a new intent, not a
            # replay, so it no longer hijacks a fresh switch.
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
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

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
            # The image actually backing the live runtime is the active profile's
            # (which may override the shared runtime.image); fall back to the
            # shared image when no profile is active yet.
            "runtime_image": active.image if active else self.catalog.runtime.get("image"),
            "state_recovery_error": state.get("state_recovery_error"),
        }

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

    async def stop_main(self) -> None:
        """Drain and stop the main runtime to reclaim its VRAM.

        The gate closes (chat fails closed / 503) and the persisted runtime_state
        becomes "stopped" so a later restart does not auto-start it.
        """
        async with self._lock:
            self.state_store.update(lambda s: s.update(gate="closed"))
            try:
                await self.backend.wait_for_drain(
                    float(self.catalog.runtime.get("drain_timeout_seconds", 30))
                )
            except Exception:  # noqa: BLE001 - deliberate stop proceeds even if drain times out
                pass
            await self.backend.stop(self.catalog)
            self.state_store.update(lambda s: s.update(runtime_state="stopped"))

    async def start_main(self) -> None:
        """Start the (stopped) main runtime with its persisted profile and validate.

        Admission against the shared GPU budget is the caller's responsibility.
        """
        async with self._lock:
            await self.backend.start(self.catalog)
            active_id = self.state_store.read().get("active_profile") or self.boot_profile
            profile = self.catalog.profiles[active_id]
            observed = await self.backend.observed_profile(self.catalog)
            if observed != active_id:
                await self.backend.replace(self.catalog, profile)
            await self.backend.validate(self.catalog, profile)

            def commit(state: dict[str, Any]) -> None:
                state["runtime_state"] = "active"
                state["gate"] = "open"

            self.state_store.update(commit)

    async def initialize(self) -> None:
        if self.state_store.read().get("runtime_state") == "stopped":
            # Respect a deliberate operator stop across restarts: leave the main
            # runtime down and the gate closed instead of reconciling it up.
            self.state_store.update(lambda s: s.update(gate="closed"))
            return
        observed = await self.backend.observed_profile(self.catalog)
        interrupted = self.state_store.read().get("last_operation")
        if interrupted and interrupted.get("status") not in _TERMINAL_STATES:
            await self._recover_interrupted(interrupted, observed)
            return
        target = self.boot_profile
        if observed == target:
            await self.backend.validate(self.catalog, self.catalog.profiles[target])
            self._commit_boot(target)
            return
        # During compose startup, first let the statically declared baseline
        # container become healthy. Replacing it while Compose is still waiting
        # on that container can invalidate dependency orchestration. Once the
        # baseline is observable, close the gate and reconcile in the background;
        # Gateway will fail closed until the persisted target validates.
        if observed in self.catalog.profiles:
            await self.backend.validate(self.catalog, self.catalog.profiles[observed])
        self.state_store.update(lambda state: state.update(gate="closed"))
        self.request_switch(target, confirm_unverified=True, boot_reconcile=True)

    async def _recover_interrupted(
        self, operation: dict[str, Any], observed: str | None
    ) -> None:
        operation_id = str(operation["id"])
        requested = operation.get("requested_profile")
        previous = operation.get("previous_profile")
        if observed == requested and requested in self.catalog.profiles:
            await self.backend.validate(self.catalog, self.catalog.profiles[requested])
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
            await self.backend.validate(self.catalog, self.catalog.profiles[previous])
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
        """Return the prior operation a request_id idempotently maps to, if any.

        A request_id is a bounded-lifetime retry key, not a permanent ledger entry.
        A prior is a live match only while it is still in flight, or for
        ``_idempotency_ttl`` seconds after it reached a terminal state. Expired
        priors are ignored entirely (neither replayed nor treated as a conflict),
        so re-using the same id after the window starts a fresh switch instead of
        resurrecting a long-past operation.
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
        lock_context.__enter__()
        reused_operation_id: str | None = None
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
        except Exception:
            lock_context.__exit__(None, None, None)
            raise
        if reused_operation_id is not None:
            lock_context.__exit__(None, None, None)
            return SwitchOutcome(reused_operation_id, True)
        task = asyncio.create_task(
            self._run(operation_id, lock_context, boot_reconcile=boot_reconcile),
            name=operation_id,
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return SwitchOutcome(operation_id, False)

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
        replaced = False
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
            replaced = True
            self._set_operation(operation_id, "starting")
            await self.backend.replace(self.catalog, target)
            self._set_operation(operation_id, "validating")
            await self.backend.validate(self.catalog, target)
        except Exception as exc:
            if not replaced:
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
            if replaced and previous_id in self.catalog.profiles:
                previous = self.catalog.profiles[previous_id]
                try:
                    await self.backend.replace(self.catalog, previous)
                    await self.backend.validate(self.catalog, previous)
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
                rollback=replaced and previous_id in self.catalog.profiles,
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
