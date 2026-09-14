from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

_ETAG_PATTERN = re.compile(r'^"config-(\d+)"$')


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


def _parse_revision(value: Any, *, param: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationValidationError(
            f"{param} must be a non-negative integer",
            param=param,
        )
    return value


def _parse_digest(value: Any, *, param: str = "plan_digest") -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ConfigurationValidationError(
            f"{param} must be a lowercase SHA-256 hex string",
            param=param,
        )
    return value


def parse_plan_request(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    body = _strict_object(
        payload,
        required={"base_revision", "changes"},
        param="body",
    )
    revision = _parse_revision(body["base_revision"], param="base_revision")
    return revision, _parse_changes(body["changes"])


def parse_apply_request(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    body = _strict_object(
        payload,
        required={"plan_digest", "changes"},
        param="body",
    )
    digest = _parse_digest(body["plan_digest"])
    return digest, _parse_changes(body["changes"])


def parse_rollback_plan_request(payload: Any) -> tuple[int, int]:
    body = _strict_object(
        payload,
        required={"base_revision", "target_revision"},
        param="body",
    )
    return (
        _parse_revision(body["base_revision"], param="base_revision"),
        _parse_revision(body["target_revision"], param="target_revision"),
    )


def parse_rollback_apply_request(payload: Any) -> tuple[int, str]:
    body = _strict_object(
        payload,
        required={"target_revision", "plan_digest"},
        param="body",
    )
    return (
        _parse_revision(body["target_revision"], param="target_revision"),
        _parse_digest(body["plan_digest"]),
    )


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


def canonical_configuration_digest(document: Mapping[str, Any]) -> str:
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


