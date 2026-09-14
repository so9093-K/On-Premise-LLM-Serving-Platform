from __future__ import annotations

import fcntl
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

import yaml

from .configuration import load_yaml_mapping
from .configuration_bindings import ConfigurationBinding, operator_runtime_bindings
from .configuration_values import validate_configuration_value
from .platform_state import operator_configuration_state_path
from .project_paths import resolve_project_root
from .runtime_configuration import RuntimeConfigurationSnapshot


OPERATOR_CONFIGURATION_VERSION = 1


class OperatorConfigurationError(RuntimeError):
    pass


class OperatorConfigurationRevisionError(OperatorConfigurationError):
    pass


@dataclass(frozen=True)
class OperatorConfigurationState:
    revision: int
    overrides: dict[str, Any]


def operator_configuration_path() -> Path | None:
    """Return the persistent override path when a platform state root is configured."""
    return operator_configuration_state_path()


def _nested_value(document: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = document
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            raise OperatorConfigurationError(
                f"Configuration Plane repository source is missing: {'.'.join(path)}"
            )
        value = value[part]
    return value


def operator_metadata_by_key(schema_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item["key"]): item
        for item in schema_items
        if item.get("owner") == "operator" and item.get("control_surface") == "configuration"
    }


def _operator_bindings(
    schema_items: list[dict[str, Any]],
) -> dict[str, ConfigurationBinding]:
    try:
        return operator_runtime_bindings(schema_items)
    except ValueError as exc:
        raise OperatorConfigurationError(str(exc)) from exc


def repository_operator_defaults(schema_items: list[dict[str, Any]]) -> dict[str, Any]:
    model_serving = load_yaml_mapping(resolve_project_root() / "configs" / "model_serving.yaml")
    return {
        key: _nested_value(model_serving, binding.repository_path)
        for key, binding in _operator_bindings(schema_items).items()
        if binding.repository_path is not None
    }


class OperatorConfigurationStore:
    """Revisioned, process-safe persistent storage for operator-owned overrides."""

    def __init__(self, path: Path, metadata_by_key: Mapping[str, dict[str, Any]]) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.metadata_by_key = dict(metadata_by_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def empty_state() -> OperatorConfigurationState:
        return OperatorConfigurationState(revision=0, overrides={})

    def _validate_document(self, document: Any) -> OperatorConfigurationState:
        if not isinstance(document, dict) or document.get("version") != OPERATOR_CONFIGURATION_VERSION:
            raise OperatorConfigurationError("unsupported operator configuration schema")
        revision = document.get("revision")
        overrides = document.get("overrides")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise OperatorConfigurationError("operator configuration revision must be a non-negative integer")
        if not isinstance(overrides, dict):
            raise OperatorConfigurationError("operator configuration overrides must be a mapping")
        if revision == 0 and overrides:
            raise OperatorConfigurationError("operator configuration revision 0 must not contain overrides")
        normalized: dict[str, Any] = {}
        for raw_key, value in overrides.items():
            if not isinstance(raw_key, str):
                raise OperatorConfigurationError("operator configuration keys must be strings")
            metadata = self.metadata_by_key.get(raw_key)
            if metadata is None:
                raise OperatorConfigurationError(
                    f"operator configuration key is not operator-owned: {raw_key}"
                )
            if metadata.get("sensitive"):
                raise OperatorConfigurationError(
                    f"sensitive configuration cannot be stored as operator override: {raw_key}"
                )
            try:
                validate_configuration_value(metadata, value)
            except ValueError as exc:
                raise OperatorConfigurationError(str(exc)) from exc
            normalized[raw_key] = value
        return OperatorConfigurationState(revision=revision, overrides=normalized)

    def _read_unlocked(self) -> OperatorConfigurationState:
        if not self.path.exists():
            return self.empty_state()
        try:
            document = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except PermissionError as exc:
            raise OperatorConfigurationError(
                f"operator configuration is not readable (permission denied): {self.path}"
            ) from exc
        except (OSError, yaml.YAMLError) as exc:
            raise OperatorConfigurationError(f"operator configuration is corrupt: {self.path}") from exc
        return self._validate_document(document)

    def read(self) -> OperatorConfigurationState:
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._read_unlocked()

    def _write_unlocked(self, state: OperatorConfigurationState) -> None:
        document = {
            "version": OPERATOR_CONFIGURATION_VERSION,
            "revision": state.revision,
            "overrides": state.overrides,
        }
        self._validate_document(document)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(document, handle, allow_unicode=True, sort_keys=True)
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

    def replace(
        self,
        overrides: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> OperatorConfigurationState:
        """Atomically replace overrides and advance the revision once."""
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self._read_unlocked()
            if current.revision != expected_revision:
                raise OperatorConfigurationRevisionError(
                    f"operator configuration revision changed: expected {expected_revision}, current {current.revision}"
                )
            candidate = self._validate_document(
                {
                    "version": OPERATOR_CONFIGURATION_VERSION,
                    "revision": current.revision + 1,
                    "overrides": dict(overrides),
                }
            )
            self._write_unlocked(candidate)
            return candidate

    def quarantine_corrupt_state(self) -> Path | None:
        """Move an unreadable state file aside; callers decide whether recovery may continue."""
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if not self.path.exists():
                return None
            target = self.path.with_name(f"{self.path.name}.corrupt.{int(time.time())}")
            os.replace(self.path, target)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return target


class ConfigurationValueResolver:
    """Resolve repository/operator/deployment layers without guessing provenance."""

    def __init__(
        self,
        *,
        repository_defaults: Mapping[str, Any],
        operator_state: OperatorConfigurationState,
        deployment_overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self._lock = RLock()
        self.repository_defaults = dict(repository_defaults)
        self._operator_state = operator_state
        self.deployment_overrides = dict(deployment_overrides or {})

    @property
    def revision(self) -> int:
        with self._lock:
            return self._operator_state.revision

    def install_operator_state(self, state: OperatorConfigurationState) -> None:
        """Install a persisted state without allowing revision/value drift."""
        with self._lock:
            current = self._operator_state
            if state.revision < current.revision:
                raise OperatorConfigurationRevisionError(
                    "configuration resolver revision cannot move backwards"
                )
            if state.revision == current.revision and state != current:
                raise OperatorConfigurationRevisionError(
                    "configuration resolver values cannot change without a new revision"
                )
            self._operator_state = state

    def resolve_many(self, keys: list[str] | tuple[str, ...]) -> tuple[int, dict[str, dict[str, Any]]]:
        """Resolve several keys under one lock so callers never mix revisions."""
        with self._lock:
            state = self._operator_state
            resolved: dict[str, dict[str, Any]] = {}
            for key in keys:
                if key not in self.repository_defaults:
                    raise KeyError(key)
                default_value = self.repository_defaults[key]
                has_operator = key in state.overrides
                operator_value = state.overrides.get(key)
                has_deployment = key in self.deployment_overrides
                if has_deployment:
                    effective_value = self.deployment_overrides[key]
                    effective_source = "deployment"
                elif has_operator:
                    effective_value = operator_value
                    effective_source = "operator"
                else:
                    effective_value = default_value
                    effective_source = "repository"
                resolved[key] = {
                    "default_value": default_value,
                    "operator_value": operator_value if has_operator else None,
                    "effective_value": effective_value,
                    "effective_source": effective_source,
                    "operator_override_shadowed": bool(has_operator and has_deployment),
                }
            return state.revision, resolved

    def resolve(self, key: str) -> dict[str, Any]:
        _, resolved = self.resolve_many((key,))
        return resolved[key]


def runtime_snapshot_from_resolver(
    resolver: ConfigurationValueResolver,
    schema_items: list[dict[str, Any]],
) -> RuntimeConfigurationSnapshot:
    bindings = _operator_bindings(schema_items)
    keys = tuple(bindings)
    revision, resolved = resolver.resolve_many(keys)
    values = {
        binding.runtime_field: resolved[key]["effective_value"]
        for key, binding in bindings.items()
        if binding.runtime_field is not None
    }
    return RuntimeConfigurationSnapshot(revision=revision, **values)
