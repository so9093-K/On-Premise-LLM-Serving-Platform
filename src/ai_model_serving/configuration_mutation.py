from __future__ import annotations

import json
from threading import RLock
from typing import Any, Mapping

from .configuration_contract import (
    ConfigurationApplyFailure,
    ConfigurationMutationError,
    ConfigurationRevisionConflict,
    ConfigurationValidationError,
    ConfigurationWriteUnavailable,
    canonical_configuration_digest,
)
from .configuration_history import (
    STABLE_AFTER_HISTORY_STATES,
    ConfigurationHistoryStore,
    configuration_history_cursor,
    parse_configuration_history_cursor,
)
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


class ConfigurationMutationEngine:
    """Plan, persist, hot-apply, verify, audit, and rollback operator configuration."""

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
            pending_count = self.history.pending_count
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
        available = verification["synchronized"] and pending_count == 0
        reason = None
        if pending_count:
            reason = "pending_operation"
        elif not verification["synchronized"]:
            reason = "state_not_synchronized"
        return {
            "available": available,
            "reason": reason,
            **verification,
            "pending_operations": pending_count,
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
        if self.history.pending_count:
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
        plan = {**digest_source, "plan_digest": canonical_configuration_digest(digest_source)}
        return plan, current, candidate_state

    def plan(self, *, base_revision: int, changes: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            plan, _, _ = self._build_plan(base_revision=base_revision, changes=changes)
            return plan

    @staticmethod
    def _history_projection(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "operation_id": record["operation_id"],
            "kind": ("configuration_rollback" if "target_revision" in record else "configuration_apply"),
            "status": record["status"],
            "phase": record["phase"],
            "actor": dict(record["actor"]),
            "request_id": record["request_id"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "base_revision": record["base_revision"],
            "candidate_revision": record["candidate_revision"],
            "applied_revision": record.get("applied_revision"),
            "target_revision": record.get("target_revision"),
            "would_change": record["would_change"],
            "plan_digest": record["plan_digest"],
            "changes": list(record["changes"]),
            "verification": dict(record["verification"]) if "verification" in record else None,
        }

    def history_page(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        if self.history is None:
            raise ConfigurationWriteUnavailable(
                "Configuration history requires persistent platform state",
                reason="persistent_state_unavailable",
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ConfigurationValidationError(
                "limit must be an integer between 1 and 200",
                param="limit",
            )
        records = self.history.records()
        records.sort(key=lambda record: (record["created_at"], record["operation_id"]), reverse=True)
        start = 0
        if cursor is not None:
            operation_id = parse_configuration_history_cursor(cursor)
            for index, record in enumerate(records):
                if record["operation_id"] == operation_id:
                    start = index + 1
                    break
            else:
                raise ConfigurationValidationError(
                    "history cursor is invalid or expired",
                    param="cursor",
                )
        remaining = records[start:]
        page = remaining[:limit]
        next_cursor = None
        if len(remaining) > limit and page:
            next_cursor = configuration_history_cursor(page[-1]["operation_id"])
        return {
            "items": [self._history_projection(record) for record in page],
            "next_cursor": next_cursor,
        }

    def _revision_snapshot(
        self,
        *,
        target_revision: int,
        current: OperatorConfigurationState,
    ) -> dict[str, Any]:
        if self.history is None:
            raise ConfigurationWriteUnavailable(
                "Configuration rollback requires persistent history",
                reason="persistent_state_unavailable",
            )
        evidence: dict[int, list[dict[str, Any]]] = {0: [{}]}

        def add(revision: int, overrides: Mapping[str, Any]) -> None:
            evidence.setdefault(revision, []).append(dict(overrides))

        add(current.revision, current.overrides)
        for record in self.history.records():
            add(record["base_revision"], record["overrides_before"])
            if record["status"] in STABLE_AFTER_HISTORY_STATES:
                verification = record.get("verification")
                if (
                    isinstance(verification, dict)
                    and verification.get("synchronized") is True
                    and record.get("applied_revision") == record["candidate_revision"]
                ):
                    add(record["candidate_revision"], record["overrides_after"])

        snapshots = evidence.get(target_revision)
        if not snapshots:
            raise ConfigurationRevisionConflict(
                f"configuration revision {target_revision} is not available for rollback",
                current_revision=current.revision,
                reason="history_revision_not_found",
            )
        unique = {
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for snapshot in snapshots
        }
        if len(unique) != 1:
            raise ConfigurationWriteUnavailable(
                f"configuration history has conflicting snapshots for revision {target_revision}",
                reason="history_revision_conflict",
            )
        return dict(snapshots[0])

    @staticmethod
    def _rollback_changes(
        current_overrides: Mapping[str, Any],
        target_overrides: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for key in sorted(set(current_overrides) | set(target_overrides)):
            current_has = key in current_overrides
            target_has = key in target_overrides
            if target_has and (not current_has or current_overrides[key] != target_overrides[key]):
                changes.append({"key": key, "op": "set", "value": target_overrides[key]})
            elif current_has and not target_has:
                changes.append({"key": key, "op": "reset"})
        return changes

    def _build_rollback_plan(
        self,
        *,
        base_revision: int,
        target_revision: int,
    ) -> tuple[dict[str, Any], OperatorConfigurationState, OperatorConfigurationState]:
        current = self._assert_write_ready()
        if current.revision != base_revision:
            raise ConfigurationRevisionConflict(
                f"configuration revision changed: expected {base_revision}, current {current.revision}",
                current_revision=current.revision,
            )
        if target_revision >= current.revision:
            reason = "rollback_already_current" if target_revision == current.revision else "history_revision_not_found"
            raise ConfigurationRevisionConflict(
                f"configuration revision {target_revision} cannot be rolled back from {current.revision}",
                current_revision=current.revision,
                reason=reason,
            )
        target_overrides = self._revision_snapshot(
            target_revision=target_revision,
            current=current,
        )
        changes = self._rollback_changes(current.overrides, target_overrides)
        if not changes:
            raise ConfigurationRevisionConflict(
                f"configuration revision {target_revision} already matches current operator state",
                current_revision=current.revision,
                reason="rollback_already_current",
            )
        base_plan, planned_current, candidate = self._build_plan(
            base_revision=base_revision,
            changes=changes,
        )
        digest_source = {
            "kind": "configuration_rollback",
            "target_revision": target_revision,
            "base_revision": base_plan["base_revision"],
            "candidate_revision": base_plan["candidate_revision"],
            "would_change": base_plan["would_change"],
            "changes": base_plan["changes"],
        }
        plan = {**digest_source, "plan_digest": canonical_configuration_digest(digest_source)}
        return plan, planned_current, candidate

    def rollback_plan(self, *, base_revision: int, target_revision: int) -> dict[str, Any]:
        with self._lock:
            plan, _, _ = self._build_rollback_plan(
                base_revision=base_revision,
                target_revision=target_revision,
            )
            return plan

    def _execute_plan(
        self,
        *,
        plan: Mapping[str, Any],
        current: OperatorConfigurationState,
        candidate: OperatorConfigurationState,
        expected_revision: int,
        reviewed_digest: str,
        actor: Mapping[str, str],
        request_id: str,
        target_revision: int | None = None,
    ) -> dict[str, Any]:
        if plan["plan_digest"] != reviewed_digest:
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
            target_revision=target_revision,
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
            result = {
                "operation_id": operation_id,
                "status": "verified",
                "changed": False,
                "revision": expected_revision,
                "verification": verification,
            }
            if target_revision is not None:
                result["target_revision"] = target_revision
            return result

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
            result = {
                "operation_id": operation_id,
                "status": "verified",
                "changed": True,
                "revision": persisted.revision,
                "verification": verification,
            }
            if target_revision is not None:
                result["target_revision"] = target_revision
            return result
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
            return self._execute_plan(
                plan=plan,
                current=current,
                candidate=candidate,
                expected_revision=expected_revision,
                reviewed_digest=plan_digest,
                actor=actor,
                request_id=request_id,
            )

    def rollback(
        self,
        *,
        expected_revision: int,
        target_revision: int,
        plan_digest: str,
        actor: Mapping[str, str],
        request_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            plan, current, candidate = self._build_rollback_plan(
                base_revision=expected_revision,
                target_revision=target_revision,
            )
            return self._execute_plan(
                plan=plan,
                current=current,
                candidate=candidate,
                expected_revision=expected_revision,
                reviewed_digest=plan_digest,
                actor=actor,
                request_id=request_id,
                target_revision=target_revision,
            )
