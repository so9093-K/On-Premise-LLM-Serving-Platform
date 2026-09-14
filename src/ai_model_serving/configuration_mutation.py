from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from .configuration_values import validate_configuration_value
from .operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationError,
    OperatorConfigurationRevisionError,
    OperatorConfigurationState,
    OperatorConfigurationStore,
    runtime_snapshot_from_resolver,
)
from .runtime_configuration import RuntimeConfigurationProvider


_HISTORY_VERSION = 1
_ETAG_PATTERN = re.compile(r'^"config-(\d+)"$')
_OPERATION_ID_PATTERN = re.compile(r"^cfg_[0-9a-f]{32}$")
_TERMINAL_HISTORY_STATES = frozenset(
    {
        "verified",
        "noop",
        "rejected",
        "apply_failed",
        "recovered_after_restart",
        "interrupted_before_persist",
        "interrupted_state_mismatch",
    }
)
_HISTORY_STATES = frozenset({"pending", *_TERMINAL_HISTORY_STATES})
_REQUIRED_HISTORY_FIELDS = frozenset(
    {
        "version",
        "operation_id",
        "kind",
        "status",
        "phase",
        "actor",
        "request_id",
        "created_at",
        "updated_at",
        "base_revision",
        "candidate_revision",
        "would_change",
        "plan_digest",
        "changes",
        "overrides_before",
        "overrides_after",
    }
)


class ConfigurationMutationError(RuntimeError):
    pass


class ConfigurationValidationError(ConfigurationMutationError):
    def __init__(self, message: str, *, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


class ConfigurationPreconditionRequired(ConfigurationMutationError):
    pass


class ConfigurationRevisionConflict(ConfigurationMutationError):
    def __init__(
        self,
        message: str,
        *,
        current_revision: int | None = None,
        reason: str = "revision_conflict",
    ) -> None:
        super().__init__(message)
        self.current_revision = current_revision
        self.reason = reason


class ConfigurationWriteUnavailable(ConfigurationMutationError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class ConfigurationApplyFailure(ConfigurationMutationError):
    def __init__(
        self,
        message: str,
        *,
        operation_id: str,
        phase: str,
        desired_revision: int | None,
        verification: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.phase = phase
        self.desired_revision = desired_revision
        self.verification = verification or {}


def configuration_etag(revision: int) -> str:
    return f'"config-{revision}"'


def parse_configuration_if_match(value: str | None) -> int:
    if value is None:
        raise ConfigurationPreconditionRequired(
            "If-Match header with the reviewed configuration revision is required"
        )
    match = _ETAG_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ConfigurationPreconditionRequired(
            'If-Match must use the configuration ETag format "config-<revision>"'
        )
    return int(match.group(1))


def _strict_object(
    value: Any,
    *,
    required: set[str],
    optional: frozenset[str] = frozenset(),
    param: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationValidationError(f"{param} must be an object", param=param)
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ConfigurationValidationError(
            f"{param} is missing required field(s): {', '.join(sorted(missing))}",
            param=param,
        )
    if unknown:
        raise ConfigurationValidationError(
            f"{param} contains unsupported field(s): {', '.join(sorted(unknown))}",
            param=param,
        )
    return value


def parse_plan_request(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    body = _strict_object(
        payload,
        required={"base_revision", "changes"},
        param="body",
    )
    revision = body["base_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ConfigurationValidationError(
            "base_revision must be a non-negative integer",
            param="base_revision",
        )
    return revision, _parse_changes(body["changes"])


def parse_apply_request(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    body = _strict_object(
        payload,
        required={"plan_digest", "changes"},
        param="body",
    )
    digest = body["plan_digest"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ConfigurationValidationError(
            "plan_digest must be a lowercase SHA-256 hex string",
            param="plan_digest",
        )
    return digest, _parse_changes(body["changes"])


def _parse_changes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ConfigurationValidationError("changes must be a non-empty array", param="changes")

    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        param = f"changes.{index}"
        item = _strict_object(
            raw,
            required={"key", "op"},
            optional=frozenset({"value"}),
            param=param,
        )
        key = item["key"]
        op = item["op"]
        if not isinstance(key, str) or not key.strip():
            raise ConfigurationValidationError("change key must be non-empty", param=f"{param}.key")
        if key in seen:
            raise ConfigurationValidationError(
                f"duplicate configuration key in one mutation: {key}",
                param=f"{param}.key",
            )
        if op not in {"set", "reset"}:
            raise ConfigurationValidationError(
                "change op must be set or reset",
                param=f"{param}.op",
            )
        if op == "set" and "value" not in item:
            raise ConfigurationValidationError(
                "set operation requires value",
                param=f"{param}.value",
            )
        if op == "reset" and "value" in item:
            raise ConfigurationValidationError(
                "reset operation must not include value",
                param=f"{param}.value",
            )
        normalized = {"key": key, "op": op}
        if op == "set":
            normalized["value"] = item["value"]
        parsed.append(normalized)
        seen.add(key)
    return parsed


def _canonical_digest(document: Mapping[str, Any]) -> str:
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _timestamp(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


class ConfigurationHistoryStore:
    """Durable operation journal for Configuration Plane mutations.

    Each operation gets one UUID-named record. It is written as ``pending`` before
    persistent desired state changes, then atomically replaced until a terminal
    state is reached. A crash therefore leaves explicit pending evidence instead of
    silently losing the audit trail. Before/after override snapshots make restart
    recovery compare actual persisted content, not revision numbers alone.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _path(self, operation_id: str) -> Path:
        return self.directory / f"{operation_id}.json"

    def _write(self, path: Path, document: Mapping[str, Any]) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _validate_record(self, path: Path, document: Any) -> dict[str, Any]:
        if not isinstance(document, dict) or document.get("version") != _HISTORY_VERSION:
            raise ConfigurationWriteUnavailable(
                f"configuration history record has unsupported format: {path}",
                reason="history_invalid",
            )
        missing = _REQUIRED_HISTORY_FIELDS - set(document)
        if missing:
            raise ConfigurationWriteUnavailable(
                f"configuration history record is missing fields: {', '.join(sorted(missing))}",
                reason="history_invalid",
            )
        operation_id = document.get("operation_id")
        if (
            not isinstance(operation_id, str)
            or _OPERATION_ID_PATTERN.fullmatch(operation_id) is None
            or path.stem != operation_id
        ):
            raise ConfigurationWriteUnavailable(
                f"configuration history operation id is invalid: {path}",
                reason="history_invalid",
            )
        if document.get("kind") != "configuration_apply" or document.get("status") not in _HISTORY_STATES:
            raise ConfigurationWriteUnavailable(
                f"configuration history kind/status is invalid: {path}",
                reason="history_invalid",
            )
        if not isinstance(document.get("phase"), str) or not document["phase"]:
            raise ConfigurationWriteUnavailable(
                f"configuration history phase is invalid: {path}",
                reason="history_invalid",
            )
        actor = document.get("actor")
        if (
            not isinstance(actor, dict)
            or set(actor) != {"auth_method", "actor_id"}
            or not all(isinstance(actor[key], str) and actor[key] for key in actor)
        ):
            raise ConfigurationWriteUnavailable(
                f"configuration history actor is invalid: {path}",
                reason="history_invalid",
            )
        if not isinstance(document.get("request_id"), str) or not document["request_id"]:
            raise ConfigurationWriteUnavailable(
                f"configuration history request id is invalid: {path}",
                reason="history_invalid",
            )
        if not _timestamp(document.get("created_at")) or not _timestamp(document.get("updated_at")):
            raise ConfigurationWriteUnavailable(
                f"configuration history timestamps are invalid: {path}",
                reason="history_invalid",
            )
        if not _non_negative_int(document.get("base_revision")) or not _non_negative_int(
            document.get("candidate_revision")
        ):
            raise ConfigurationWriteUnavailable(
                f"configuration history revisions are invalid: {path}",
                reason="history_invalid",
            )
        if not isinstance(document.get("would_change"), bool):
            raise ConfigurationWriteUnavailable(
                f"configuration history would_change is invalid: {path}",
                reason="history_invalid",
            )
        digest = document.get("plan_digest")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ConfigurationWriteUnavailable(
                f"configuration history plan digest is invalid: {path}",
                reason="history_invalid",
            )
        if not isinstance(document.get("changes"), list) or not document["changes"]:
            raise ConfigurationWriteUnavailable(
                f"configuration history changes are invalid: {path}",
                reason="history_invalid",
            )
        if not isinstance(document.get("overrides_before"), dict) or not isinstance(
            document.get("overrides_after"), dict
        ):
            raise ConfigurationWriteUnavailable(
                f"configuration history override snapshots are invalid: {path}",
                reason="history_invalid",
            )
        if "applied_revision" in document:
            applied_revision = document["applied_revision"]
            if applied_revision is not None and not _non_negative_int(applied_revision):
                raise ConfigurationWriteUnavailable(
                    f"configuration history applied revision is invalid: {path}",
                    reason="history_invalid",
                )
        if "verification" in document and not isinstance(document["verification"], dict):
            raise ConfigurationWriteUnavailable(
                f"configuration history verification is invalid: {path}",
                reason="history_invalid",
            )
        if "error" in document and not isinstance(document["error"], str):
            raise ConfigurationWriteUnavailable(
                f"configuration history error is invalid: {path}",
                reason="history_invalid",
            )
        return document

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationWriteUnavailable(
                f"configuration history is unreadable: {path}",
                reason="history_unreadable",
            ) from exc
        return self._validate_record(path, document)

    def begin(
        self,
        *,
        plan: Mapping[str, Any],
        overrides_before: Mapping[str, Any],
        overrides_after: Mapping[str, Any],
        actor: Mapping[str, str],
        request_id: str,
    ) -> str:
        operation_id = f"cfg_{uuid4().hex}"
        now = time.time()
        record = {
            "version": _HISTORY_VERSION,
            "operation_id": operation_id,
            "kind": "configuration_apply",
            "status": "pending",
            "phase": "planned",
            "actor": dict(actor),
            "request_id": request_id,
            "created_at": now,
            "updated_at": now,
            "base_revision": plan["base_revision"],
            "candidate_revision": plan["candidate_revision"],
            "would_change": plan["would_change"],
            "plan_digest": plan["plan_digest"],
            "changes": plan["changes"],
            "overrides_before": dict(overrides_before),
            "overrides_after": dict(overrides_after),
        }
        with self._lock:
            path = self._path(operation_id)
            if path.exists():
                raise ConfigurationWriteUnavailable(
                    "configuration history operation id collision",
                    reason="history_collision",
                )
            self._validate_record(path, record)
            self._write(path, record)
        return operation_id

    def finish(
        self,
        operation_id: str,
        *,
        status: str,
        phase: str,
        applied_revision: int | None,
        verification: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in _TERMINAL_HISTORY_STATES:
            raise ValueError(f"invalid configuration history terminal state: {status}")
        with self._lock:
            path = self._path(operation_id)
            record = self._read(path)
            if record.get("status") in _TERMINAL_HISTORY_STATES:
                raise ConfigurationWriteUnavailable(
                    f"configuration history operation is already terminal: {operation_id}",
                    reason="history_already_terminal",
                )
            record.update(
                status=status,
                phase=phase,
                updated_at=time.time(),
                applied_revision=applied_revision,
                verification=dict(verification or {}),
            )
            if error is not None:
                record["error"] = error
            self._validate_record(path, record)
            self._write(path, record)
            return record

    def pending_records(self) -> list[dict[str, Any]]:
        with self._lock:
            pending: list[dict[str, Any]] = []
            for path in sorted(self.directory.glob("cfg_*.json")):
                record = self._read(path)
                if record["status"] == "pending":
                    pending.append(record)
            return pending


class ConfigurationMutationEngine:
    """Plan, persist, hot-apply, verify, and audit operator configuration changes."""

    def __init__(
        self,
        *,
        schema_items: list[dict[str, Any]],
        store: OperatorConfigurationStore | None,
        resolver: ConfigurationValueResolver,
        runtime_configuration: RuntimeConfigurationProvider,
        history: ConfigurationHistoryStore | None,
    ) -> None:
        self.schema_items = schema_items
        self.store = store
        self.resolver = resolver
        self.runtime_configuration = runtime_configuration
        self.history = history
        self._lock = RLock()
        self._metadata = {str(item["key"]): item for item in schema_items}
        self._operator_keys = tuple(
            str(item["key"])
            for item in schema_items
            if item.get("owner") == "operator"
            and item.get("control_surface") == "configuration"
        )

    def _resolver_overrides(self) -> tuple[int, dict[str, Any]]:
        revision, resolved = self.resolver.resolve_many(self._operator_keys)
        overrides = {
            key: item["operator_value"]
            for key, item in resolved.items()
            if item["operator_value"] is not None
        }
        return revision, overrides

    def _read_store(self) -> OperatorConfigurationState:
        if self.store is None:
            raise ConfigurationWriteUnavailable(
                "Configuration write plane requires persistent platform state",
                reason="persistent_state_unavailable",
            )
        try:
            return self.store.read()
        except OperatorConfigurationError as exc:
            raise ConfigurationWriteUnavailable(
                "Operator configuration state is unreadable or invalid",
                reason="operator_state_unavailable",
            ) from exc

    def _verification(self) -> dict[str, Any]:
        if self.store is None:
            return {
                "store_revision": None,
                "resolver_revision": self.resolver.revision,
                "runtime_revision": self.runtime_configuration.snapshot().revision,
                "synchronized": False,
            }
        persisted = self._read_store()
        resolver_revision, resolver_overrides = self._resolver_overrides()
        runtime_snapshot = self.runtime_configuration.snapshot()
        try:
            expected_runtime = runtime_snapshot_from_resolver(self.resolver, self.schema_items)
            runtime_matches = runtime_snapshot == expected_runtime
        except Exception:
            runtime_matches = False
        synchronized = (
            persisted.revision == resolver_revision
            and persisted.overrides == resolver_overrides
            and runtime_matches
        )
        return {
            "store_revision": persisted.revision,
            "resolver_revision": resolver_revision,
            "runtime_revision": runtime_snapshot.revision,
            "synchronized": synchronized,
        }

    def status(self) -> dict[str, Any]:
        if self.store is None or self.history is None:
            return {
                "available": False,
                "reason": "persistent_state_unavailable",
                **self._verification(),
                "pending_operations": None,
            }
        try:
            pending = self.history.pending_records()
            verification = self._verification()
        except ConfigurationMutationError as exc:
            return {
                "available": False,
                "reason": getattr(exc, "reason", "state_unavailable"),
                "store_revision": None,
                "resolver_revision": self.resolver.revision,
                "runtime_revision": self.runtime_configuration.snapshot().revision,
                "synchronized": False,
                "pending_operations": None,
            }
        available = verification["synchronized"] and not pending
        reason = None
        if pending:
            reason = "pending_operation"
        elif not verification["synchronized"]:
            reason = "state_not_synchronized"
        return {
            "available": available,
            "reason": reason,
            **verification,
            "pending_operations": len(pending),
        }

    def recover_interrupted_operations(self) -> None:
        if self.history is None or self.store is None:
            return
        verification = self._verification()
        persisted = self._read_store()
        for record in self.history.pending_records():
            before_matches = (
                persisted.revision == record["base_revision"]
                and persisted.overrides == record["overrides_before"]
            )
            after_matches = (
                persisted.revision == record["candidate_revision"]
                and persisted.overrides == record["overrides_after"]
            )
            if after_matches and verification["synchronized"]:
                status = "recovered_after_restart"
            elif before_matches and record["would_change"]:
                status = "interrupted_before_persist"
            elif before_matches and not record["would_change"] and verification["synchronized"]:
                status = "recovered_after_restart"
            else:
                status = "interrupted_state_mismatch"
            self.history.finish(
                record["operation_id"],
                status=status,
                phase="recovery",
                applied_revision=persisted.revision,
                verification=verification,
            )

    def _assert_write_ready(self) -> OperatorConfigurationState:
        if self.store is None or self.history is None:
            raise ConfigurationWriteUnavailable(
                "Configuration write plane requires persistent platform state",
                reason="persistent_state_unavailable",
            )
        pending = self.history.pending_records()
        if pending:
            raise ConfigurationWriteUnavailable(
                "Configuration write plane has an unfinished operation; restart or inspect history before retrying",
                reason="pending_operation",
            )
        verification = self._verification()
        if not verification["synchronized"]:
            raise ConfigurationWriteUnavailable(
                "Configuration store, resolver, and runtime snapshot are not synchronized",
                reason="state_not_synchronized",
            )
        return self._read_store()

    def _editable_metadata(self, key: str) -> dict[str, Any]:
        metadata = self._metadata.get(key)
        if metadata is None:
            raise ConfigurationValidationError(
                f"unknown configuration key: {key}",
                param="changes.key",
            )
        if (
            metadata.get("owner") != "operator"
            or metadata.get("control_surface") != "configuration"
            or not metadata.get("editable")
        ):
            raise ConfigurationValidationError(
                f"configuration key is not editable through Configuration Plane: {key}",
                param="changes.key",
            )
        if metadata.get("sensitive"):
            raise ConfigurationValidationError(
                f"sensitive configuration cannot use generic mutation: {key}",
                param="changes.key",
            )
        if metadata.get("apply_mode") != "hot_reload":
            raise ConfigurationWriteUnavailable(
                f"configuration apply mode is not implemented: {metadata.get('apply_mode')}",
                reason="apply_mode_unavailable",
            )
        return metadata

    def _build_plan(
        self,
        *,
        base_revision: int,
        changes: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], OperatorConfigurationState, OperatorConfigurationState]:
        current = self._assert_write_ready()
        if current.revision != base_revision:
            raise ConfigurationRevisionConflict(
                f"configuration revision changed: expected {base_revision}, current {current.revision}",
                current_revision=current.revision,
            )

        candidate_overrides = dict(current.overrides)
        metadata_by_key: dict[str, dict[str, Any]] = {}
        for index, change in enumerate(changes):
            key = change["key"]
            metadata = self._editable_metadata(key)
            metadata_by_key[key] = metadata
            if change["op"] == "set":
                try:
                    validate_configuration_value(metadata, change["value"])
                except ValueError as exc:
                    raise ConfigurationValidationError(
                        str(exc),
                        param=f"changes.{index}.value",
                    ) from exc
                candidate_overrides[key] = change["value"]
            else:
                candidate_overrides.pop(key, None)

        would_change = candidate_overrides != current.overrides
        candidate_revision = current.revision + (1 if would_change else 0)
        candidate_state = OperatorConfigurationState(
            revision=candidate_revision,
            overrides=candidate_overrides,
        )

        current_revision, before = self.resolver.resolve_many(
            tuple(change["key"] for change in changes)
        )
        if current_revision != current.revision:
            raise ConfigurationWriteUnavailable(
                "Configuration resolver changed while planning",
                reason="state_not_synchronized",
            )
        preview = ConfigurationValueResolver(
            repository_defaults=self.resolver.repository_defaults,
            operator_state=candidate_state,
            deployment_overrides=self.resolver.deployment_overrides,
        )
        _, after = preview.resolve_many(tuple(change["key"] for change in changes))

        plan_changes: list[dict[str, Any]] = []
        for change in changes:
            key = change["key"]
            before_item = before[key]
            after_item = after[key]
            metadata = metadata_by_key[key]
            plan_changes.append(
                {
                    "key": key,
                    "operation": change["op"],
                    "operator_before": before_item["operator_value"],
                    "operator_after": after_item["operator_value"],
                    "effective_before": before_item["effective_value"],
                    "effective_after": after_item["effective_value"],
                    "effective_source_before": before_item["effective_source"],
                    "effective_source_after": after_item["effective_source"],
                    "operator_override_shadowed_after": after_item["operator_override_shadowed"],
                    "apply_mode": metadata["apply_mode"],
                    "risk": metadata["risk"],
                    "no_effective_change": before_item["effective_value"]
                    == after_item["effective_value"],
                }
            )

        digest_source = {
            "base_revision": current.revision,
            "candidate_revision": candidate_revision,
            "would_change": would_change,
            "changes": plan_changes,
        }
        plan = {**digest_source, "plan_digest": _canonical_digest(digest_source)}
        return plan, current, candidate_state

    def plan(self, *, base_revision: int, changes: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            plan, _, _ = self._build_plan(base_revision=base_revision, changes=changes)
            return plan

    def apply(
        self,
        *,
        expected_revision: int,
        changes: list[dict[str, Any]],
        plan_digest: str,
        actor: Mapping[str, str],
        request_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            plan, current, candidate = self._build_plan(
                base_revision=expected_revision,
                changes=changes,
            )
            if plan["plan_digest"] != plan_digest:
                raise ConfigurationRevisionConflict(
                    "reviewed configuration plan no longer matches the current plan",
                    current_revision=expected_revision,
                    reason="plan_digest_mismatch",
                )
            assert self.history is not None
            operation_id = self.history.begin(
                plan=plan,
                overrides_before=current.overrides,
                overrides_after=candidate.overrides,
                actor=actor,
                request_id=request_id,
            )

            if not plan["would_change"]:
                verification = self._verification()
                self.history.finish(
                    operation_id,
                    status="noop",
                    phase="verified",
                    applied_revision=expected_revision,
                    verification=verification,
                )
                return {
                    "operation_id": operation_id,
                    "status": "verified",
                    "changed": False,
                    "revision": expected_revision,
                    "verification": verification,
                }

            persisted: OperatorConfigurationState | None = None
            phase = "persisting"
            try:
                assert self.store is not None
                persisted = self.store.replace(
                    candidate.overrides,
                    expected_revision=expected_revision,
                )
                phase = "applying"
                self.resolver.install_operator_state(persisted)
                runtime_snapshot = runtime_snapshot_from_resolver(self.resolver, self.schema_items)
                self.runtime_configuration.install(runtime_snapshot)
                phase = "verifying"
                verification = self._verification()
                if not verification["synchronized"]:
                    raise RuntimeError("configuration state did not converge after apply")
                phase = "history"
                self.history.finish(
                    operation_id,
                    status="verified",
                    phase="verified",
                    applied_revision=persisted.revision,
                    verification=verification,
                )
                return {
                    "operation_id": operation_id,
                    "status": "verified",
                    "changed": True,
                    "revision": persisted.revision,
                    "verification": verification,
                }
            except OperatorConfigurationRevisionError as exc:
                verification = self._verification()
                try:
                    self.history.finish(
                        operation_id,
                        status="rejected",
                        phase="persisting",
                        applied_revision=verification.get("store_revision"),
                        verification=verification,
                        error=str(exc),
                    )
                except Exception as history_exc:
                    raise ConfigurationApplyFailure(
                        "configuration revision conflict could not be finalized in history",
                        operation_id=operation_id,
                        phase="history",
                        desired_revision=verification.get("store_revision"),
                        verification=verification,
                    ) from history_exc
                raise ConfigurationRevisionConflict(
                    str(exc),
                    current_revision=verification.get("store_revision"),
                ) from exc
            except Exception as exc:
                verification = self._verification()
                try:
                    self.history.finish(
                        operation_id,
                        status="apply_failed",
                        phase=phase,
                        applied_revision=(persisted.revision if persisted is not None else None),
                        verification=verification,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                except Exception:
                    # The pending record written before mutation remains durable. Future
                    # writes are blocked until restart recovery can reconcile it.
                    pass
                raise ConfigurationApplyFailure(
                    "configuration apply did not complete successfully",
                    operation_id=operation_id,
                    phase=phase,
                    desired_revision=(persisted.revision if persisted is not None else None),
                    verification=verification,
                ) from exc