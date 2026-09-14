from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from .configuration_contract import ConfigurationValidationError, ConfigurationWriteUnavailable

_HISTORY_VERSION = 1
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
STABLE_AFTER_HISTORY_STATES = frozenset({"verified", "noop", "recovered_after_restart"})
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


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _timestamp(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def configuration_history_cursor(operation_id: str) -> str:
    payload = f"v1:{operation_id}".encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def parse_configuration_history_cursor(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationValidationError("history cursor is invalid", param="cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigurationValidationError("history cursor is invalid", param="cursor") from exc
    if not decoded.startswith("v1:"):
        raise ConfigurationValidationError("history cursor is invalid", param="cursor")
    operation_id = decoded[3:]
    if _OPERATION_ID_PATTERN.fullmatch(operation_id) is None:
        raise ConfigurationValidationError("history cursor is invalid", param="cursor")
    return operation_id


class ConfigurationHistoryStore:
    """Durable operation journal for Configuration Plane mutations.

    Each operation gets one UUID-named record. It is written as ``pending`` before
    persistent desired state changes, then atomically replaced until a terminal
    state is reached. A crash therefore leaves explicit pending evidence instead of
    silently losing the audit trail. Before/after override snapshots make restart
    recovery and rollback compare actual persisted content, not revision numbers alone.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        # Durable JSON records remain the audit SoT. The pending-id index is only
        # a process-local read model so readiness checks do not rescan every
        # terminal history record. It is rebuilt from the journal after restart.
        self._pending_operation_ids: set[str] | None = None
        self._history_directory_identity: tuple[int, int] | None = None

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
        target_revision = document.get("target_revision")
        if "target_revision" in document and not _non_negative_int(target_revision):
            raise ConfigurationWriteUnavailable(
                f"configuration rollback target revision is invalid: {path}",
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
        if "target_revision" in document and target_revision >= document["base_revision"]:
            raise ConfigurationWriteUnavailable(
                f"configuration rollback target must precede base revision: {path}",
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

    def _directory_identity(self) -> tuple[int, int]:
        try:
            stat = self.directory.stat()
        except OSError as exc:
            raise ConfigurationWriteUnavailable(
                "configuration history directory is unavailable",
                reason="history_unreadable",
            ) from exc
        return stat.st_mtime_ns, stat.st_ctime_ns

    def _rebuild_pending_index(self) -> set[str]:
        before = self._directory_identity()
        pending: set[str] = set()
        for path in sorted(self.directory.glob("cfg_*.json")):
            record = self._read(path)
            if record["status"] == "pending":
                pending.add(record["operation_id"])
        after = self._directory_identity()
        if after != before:
            raise ConfigurationWriteUnavailable(
                "configuration history changed while rebuilding the pending index",
                reason="history_changed_during_scan",
            )
        self._pending_operation_ids = pending
        self._history_directory_identity = after
        return pending

    def _ensure_pending_index(self) -> set[str]:
        identity = self._directory_identity()
        if (
            self._pending_operation_ids is None
            or self._history_directory_identity != identity
        ):
            return self._rebuild_pending_index()
        return self._pending_operation_ids

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._ensure_pending_index())

    def begin(
        self,
        *,
        plan: Mapping[str, Any],
        overrides_before: Mapping[str, Any],
        overrides_after: Mapping[str, Any],
        actor: Mapping[str, str],
        request_id: str,
        target_revision: int | None = None,
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
        if target_revision is not None:
            record["target_revision"] = target_revision
        with self._lock:
            pending = self._ensure_pending_index()
            path = self._path(operation_id)
            if path.exists():
                raise ConfigurationWriteUnavailable(
                    "configuration history operation id collision",
                    reason="history_collision",
                )
            self._validate_record(path, record)
            self._write(path, record)
            pending.add(operation_id)
            self._history_directory_identity = self._directory_identity()
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
            pending = self._ensure_pending_index()
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
            pending.discard(operation_id)
            self._history_directory_identity = self._directory_identity()
            return record

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._read(path) for path in sorted(self.directory.glob("cfg_*.json"))]

    def pending_records(self) -> list[dict[str, Any]]:
        with self._lock:
            pending = self._ensure_pending_index()
            return [self._read(self._path(operation_id)) for operation_id in sorted(pending)]


