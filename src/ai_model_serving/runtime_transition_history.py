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

_HISTORY_VERSION = 1
_OPERATION_ID_PATTERN = re.compile(r"^rt_[0-9a-f]{32}$")
_TERMINAL_STATES = frozenset(
    {
        "verified",
        "noop",
        "rejected",
        "apply_failed",
        "verification_failed",
        "interrupted_after_restart",
    }
)
_HISTORY_STATES = frozenset({"pending", *_TERMINAL_STATES})


class RuntimeTransitionHistoryUnavailable(RuntimeError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class RuntimeTransitionHistoryNotFound(LookupError):
    pass


class RuntimeTransitionHistoryCursorError(ValueError):
    pass


def runtime_transition_history_cursor(operation_id: str) -> str:
    payload = f"v1:{operation_id}".encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def parse_runtime_transition_history_cursor(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeTransitionHistoryCursorError("runtime history cursor is invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeTransitionHistoryCursorError("runtime history cursor is invalid") from exc
    if not decoded.startswith("v1:"):
        raise RuntimeTransitionHistoryCursorError("runtime history cursor is invalid")
    operation_id = decoded[3:]
    if _OPERATION_ID_PATTERN.fullmatch(operation_id) is None:
        raise RuntimeTransitionHistoryCursorError("runtime history cursor is invalid")
    return operation_id


def _timestamp(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


class RuntimeTransitionHistoryStore:
    """Runtime transition audit journal with a process-local read model.

    Deployed Gateway instances use a persistent directory next to the runtime desired-state
    file. Source-tree/test apps without a configured platform state root keep the same API
    contract in memory, while every persisted record uses temp+fsync+atomic replace.

    Persistent JSON records remain the audit source of truth. The process-local mapping is a
    read model: normal reads only stat the directory, while external additions/deletions or
    atomic replacements invalidate that mapping and force a full journal revalidation. A
    changed, missing, unreadable, or malformed journal fails closed instead of serving stale
    in-memory evidence.

    HTTP operation ids are never converted into filesystem paths. Persistent paths are built
    only from internally generated ids and indexed from validated directory entries, so read
    routes perform trusted mapping lookup instead of user-controlled path construction.
    """

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._paths: dict[str, Path] = {}
        self._directory_identity: tuple[int, int] | None = None
        self._unavailable_reason: str | None = None
        if directory is None:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self._reload_from_directory()
        except RuntimeTransitionHistoryUnavailable as exc:
            self._unavailable_reason = exc.reason
        except OSError:
            self._unavailable_reason = "history_unreadable"

    @property
    def durable(self) -> bool:
        return self.directory is not None

    @property
    def available(self) -> bool:
        return self._unavailable_reason is None

    def _assert_available(self) -> None:
        if self._unavailable_reason is not None:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history is unavailable",
                reason=self._unavailable_reason,
            )

    def _directory_identity_value(self) -> tuple[int, int]:
        if self.directory is None:
            raise RuntimeError("in-memory runtime history has no directory identity")
        try:
            stat = self.directory.stat()
            if not self.directory.is_dir():
                raise OSError("runtime history path is not a directory")
        except OSError as exc:
            self._unavailable_reason = "history_unreadable"
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history directory is unavailable",
                reason="history_unreadable",
            ) from exc
        return (stat.st_mtime_ns, stat.st_ctime_ns)

    def _reload_from_directory(self) -> None:
        if self.directory is None:
            return
        before = self._directory_identity_value()
        records: dict[str, dict[str, Any]] = {}
        paths: dict[str, Path] = {}
        try:
            candidates = sorted(self.directory.glob("rt_*.json"))
        except OSError as exc:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history directory is unreadable",
                reason="history_unreadable",
            ) from exc
        for path in candidates:
            if path.is_symlink() or not path.is_file():
                raise RuntimeTransitionHistoryUnavailable(
                    "runtime transition history contains an unsafe record path",
                    reason="history_invalid",
                )
            record = self._read_path(path)
            operation_id = record["operation_id"]
            if operation_id in records:
                raise RuntimeTransitionHistoryUnavailable(
                    "runtime transition history contains duplicate operation ids",
                    reason="history_invalid",
                )
            records[operation_id] = record
            paths[operation_id] = path
        after = self._directory_identity_value()
        if before != after:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history changed while it was being validated",
                reason="history_changed_during_scan",
            )
        self._records = records
        self._paths = paths
        self._directory_identity = after

    def _ensure_directory_consistent(self) -> None:
        self._assert_available()
        if self.directory is None:
            return
        try:
            current = self._directory_identity_value()
            if self._directory_identity != current:
                self._reload_from_directory()
        except RuntimeTransitionHistoryUnavailable as exc:
            self._unavailable_reason = exc.reason
            raise

    def _validate_record(self, document: Any, *, expected_id: str | None = None) -> dict[str, Any]:
        if not isinstance(document, dict) or document.get("version") != _HISTORY_VERSION:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history record has unsupported format",
                reason="history_invalid",
            )
        required = {
            "version",
            "operation_id",
            "kind",
            "status",
            "phase",
            "actor",
            "request_id",
            "created_at",
            "updated_at",
            "service_key",
            "desired_state",
            "force",
            "reviewed_plan",
            "plan_digest",
            "before",
            "apply_result",
            "verification",
            "error",
            "durable",
        }
        if set(document) != required:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history record fields are invalid",
                reason="history_invalid",
            )
        operation_id = document.get("operation_id")
        if (
            not isinstance(operation_id, str)
            or _OPERATION_ID_PATTERN.fullmatch(operation_id) is None
            or (expected_id is not None and operation_id != expected_id)
        ):
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history operation id is invalid",
                reason="history_invalid",
            )
        if document.get("kind") != "runtime_transition" or document.get("status") not in _HISTORY_STATES:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history kind/status is invalid",
                reason="history_invalid",
            )
        if not isinstance(document.get("phase"), str) or not document["phase"]:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history phase is invalid",
                reason="history_invalid",
            )
        actor = document.get("actor")
        if (
            not isinstance(actor, dict)
            or set(actor) != {"auth_method", "actor_id"}
            or not all(isinstance(actor[key], str) and actor[key] for key in actor)
        ):
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history actor is invalid",
                reason="history_invalid",
            )
        if not isinstance(document.get("request_id"), str) or not document["request_id"]:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history request id is invalid",
                reason="history_invalid",
            )
        if not _timestamp(document.get("created_at")) or not _timestamp(document.get("updated_at")):
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history timestamps are invalid",
                reason="history_invalid",
            )
        if not isinstance(document.get("service_key"), str) or not document["service_key"]:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history service key is invalid",
                reason="history_invalid",
            )
        if document.get("desired_state") not in {"active", "stopped"}:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history desired state is invalid",
                reason="history_invalid",
            )
        if not isinstance(document.get("force"), bool) or not isinstance(document.get("reviewed_plan"), bool):
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history transition flags are invalid",
                reason="history_invalid",
            )
        digest = document.get("plan_digest")
        if digest is not None and (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history plan digest is invalid",
                reason="history_invalid",
            )
        if document["reviewed_plan"] != (digest is not None):
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history reviewed-plan marker is inconsistent",
                reason="history_invalid",
            )
        if not isinstance(document.get("before"), dict):
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history before snapshot is invalid",
                reason="history_invalid",
            )
        for field in ("apply_result", "verification", "error"):
            value = document.get(field)
            if value is not None and not isinstance(value, dict):
                raise RuntimeTransitionHistoryUnavailable(
                    f"runtime transition history {field} is invalid",
                    reason="history_invalid",
                )
        if not isinstance(document.get("durable"), bool) or document["durable"] is not self.durable:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history durability marker is invalid",
                reason="history_invalid",
            )
        return document

    def _read_path(self, path: Path) -> dict[str, Any]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history is unreadable",
                reason="history_unreadable",
            ) from exc
        return self._validate_record(document, expected_id=path.stem)

    def _write_path(self, path: Path, document: Mapping[str, Any]) -> None:
        if self.directory is None:
            return
        try:
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
            self._directory_identity = self._directory_identity_value()
        except (OSError, RuntimeTransitionHistoryUnavailable) as exc:
            self._unavailable_reason = (
                exc.reason if isinstance(exc, RuntimeTransitionHistoryUnavailable) else "history_unwritable"
            )
            raise RuntimeTransitionHistoryUnavailable(
                "runtime transition history could not be persisted",
                reason=self._unavailable_reason,
            ) from exc

    def begin(
        self,
        *,
        service_key: str,
        desired_state: str,
        force: bool,
        plan_digest: str | None,
        actor: Mapping[str, str],
        request_id: str,
        before: Mapping[str, Any],
    ) -> str:
        with self._lock:
            self._ensure_directory_consistent()
            operation_id = f"rt_{uuid4().hex}"
            now = time.time()
            record = {
                "version": _HISTORY_VERSION,
                "operation_id": operation_id,
                "kind": "runtime_transition",
                "status": "pending",
                "phase": "planned",
                "actor": dict(actor),
                "request_id": request_id,
                "created_at": now,
                "updated_at": now,
                "service_key": service_key,
                "desired_state": desired_state,
                "force": force,
                "reviewed_plan": plan_digest is not None,
                "plan_digest": plan_digest,
                "before": dict(before),
                "apply_result": None,
                "verification": None,
                "error": None,
                "durable": self.durable,
            }
            self._validate_record(record)
            path = self.directory / f"{operation_id}.json" if self.directory is not None else None
            if path is not None:
                self._write_path(path, record)
                self._paths[operation_id] = path
            self._records[operation_id] = record
            return operation_id

    def finish(
        self,
        operation_id: str,
        *,
        status: str,
        phase: str,
        apply_result: Mapping[str, Any] | None,
        verification: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in _TERMINAL_STATES:
            raise ValueError(f"invalid runtime transition terminal state: {status}")
        with self._lock:
            self._ensure_directory_consistent()
            record = self._records.get(operation_id)
            if record is None:
                raise RuntimeTransitionHistoryNotFound(operation_id)
            if record["status"] in _TERMINAL_STATES:
                raise RuntimeTransitionHistoryUnavailable(
                    "runtime transition history operation is already terminal",
                    reason="history_already_terminal",
                )
            updated = {
                **record,
                "status": status,
                "phase": phase,
                "updated_at": time.time(),
                "apply_result": dict(apply_result) if apply_result is not None else None,
                "verification": dict(verification) if verification is not None else None,
                "error": dict(error) if error is not None else None,
            }
            self._validate_record(updated)
            path = self._paths.get(operation_id)
            if path is not None:
                self._write_path(path, updated)
            self._records[operation_id] = updated
            return dict(updated)

    def get(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_directory_consistent()
            if _OPERATION_ID_PATTERN.fullmatch(operation_id) is None:
                raise RuntimeTransitionHistoryNotFound(operation_id)
            record = self._records.get(operation_id)
            if record is None:
                raise RuntimeTransitionHistoryNotFound(operation_id)
            return dict(record)

    def page(self, *, limit: int, cursor: str | None = None) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise RuntimeTransitionHistoryCursorError("runtime history limit must be between 1 and 200")
        with self._lock:
            self._ensure_directory_consistent()
            ordered = sorted(
                self._records.values(),
                key=lambda record: (record["created_at"], record["operation_id"]),
                reverse=True,
            )
            start = 0
            if cursor is not None:
                operation_id = parse_runtime_transition_history_cursor(cursor)
                for index, record in enumerate(ordered):
                    if record["operation_id"] == operation_id:
                        start = index + 1
                        break
                else:
                    raise RuntimeTransitionHistoryCursorError("runtime history cursor is stale")
            page = ordered[start : start + limit]
            has_more = start + limit < len(ordered)
            next_cursor = (
                runtime_transition_history_cursor(page[-1]["operation_id"])
                if has_more and page
                else None
            )
            return {
                "items": [dict(record) for record in page],
                "next_cursor": next_cursor,
            }

    def recover_interrupted_operations(self) -> int:
        with self._lock:
            self._ensure_directory_consistent()
            pending_ids = [
                operation_id
                for operation_id, record in self._records.items()
                if record["status"] == "pending"
            ]
        for operation_id in pending_ids:
            self.finish(
                operation_id,
                status="interrupted_after_restart",
                phase="interrupted",
                apply_result=None,
                verification={
                    "converged": False,
                    "reason": "process_restarted_before_terminal_record",
                },
                error={"code": "INTERRUPTED_AFTER_RESTART"},
            )
        return len(pending_ids)
