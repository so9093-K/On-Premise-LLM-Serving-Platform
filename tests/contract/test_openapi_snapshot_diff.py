from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any

import yaml

from scripts.validation.openapi_snapshot_diff import main, _compare_one


def _minimal_openapi(*, description: str = "설명", version: str = "0.0.1") -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test", "description": description, "version": version},
        "paths": {},
        "components": {"securitySchemes": {}},
    }


def _with_post(base: dict[str, Any], path: str, operation: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(base)
    doc.setdefault("paths", {})[path] = {"post": operation}
    return doc


def _op(*, summary: str = "요약", description: str = "", operation_id: str = "opId",
        examples: dict[str, Any] | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {
        "tags": ["T"],
        "summary": summary,
        "operationId": operation_id,
        "responses": {"200": {"description": "ok"}},
    }
    if description:
        op["description"] = description
    if examples is not None:
        op["requestBody"] = {
            "content": {"application/json": {"examples": examples}}
        }
    return op


def _write_yaml(data: dict[str, Any]) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False,
                                    dir=Path("."), encoding="utf-8")
    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    f.close()
    return f.name


def test_compare_info_description_mismatch_is_detected() -> None:
    generated = _minimal_openapi(description="생성된 설명")
    static = _minimal_openapi(description="다른 설명")
    rel = _write_yaml(static)
    try:
        issues = _compare_one("svc", rel, generated)
        assert any("info.description mismatch" in i for i in issues), \
            f"Expected info.description mismatch, got: {issues}"
    finally:
        Path(rel).unlink(missing_ok=True)


def test_compare_info_description_match_produces_no_issue() -> None:
    doc = _minimal_openapi(description="동일한 설명")
    rel = _write_yaml(doc)
    try:
        issues = _compare_one("svc", rel, doc)
        assert not any("info.description" in i for i in issues), \
            f"Unexpected description issue: {issues}"
    finally:
        Path(rel).unlink(missing_ok=True)


def test_compare_request_example_key_mismatch_is_detected() -> None:
    generated = _with_post(
        _minimal_openapi(),
        "/v1/chat",
        _op(examples={"basic": {"summary": "기본", "value": {"a": 1}},
                       "json_object": {"summary": "json", "value": {"b": 2}}}),
    )
    static = _with_post(
        _minimal_openapi(),
        "/v1/chat",
        _op(examples={"basic": {"summary": "기본", "value": {"a": 1}},
                       "with_system_prompt": {"summary": "시스템", "value": {"c": 3}}}),
    )
    rel = _write_yaml(static)
    try:
        issues = _compare_one("svc", rel, generated)
        assert any("request examples key mismatch" in i for i in issues), \
            f"Expected key mismatch, got: {issues}"
        assert any("with_system_prompt" in i for i in issues), \
            f"Expected static_only mention, got: {issues}"
        assert any("json_object" in i for i in issues), \
            f"Expected generated_only mention, got: {issues}"
    finally:
        Path(rel).unlink(missing_ok=True)


def test_compare_request_example_value_mismatch_is_detected() -> None:
    generated = _with_post(
        _minimal_openapi(),
        "/v1/chat",
        _op(examples={"basic": {"summary": "기본", "value": {"model": "local-main"}}}),
    )
    static = _with_post(
        _minimal_openapi(),
        "/v1/chat",
        _op(examples={"basic": {"summary": "기본", "value": {"model": "OLD-model"}}}),
    )
    rel = _write_yaml(static)
    try:
        issues = _compare_one("svc", rel, generated)
        assert any("request example 'basic' value mismatch" in i for i in issues), \
            f"Expected value mismatch, got: {issues}"
    finally:
        Path(rel).unlink(missing_ok=True)


def test_compare_request_examples_match_produces_no_issue() -> None:
    examples = {"basic": {"summary": "기본", "value": {"model": "local-main"}}}
    doc = _with_post(_minimal_openapi(), "/v1/chat", _op(examples=examples))
    rel = _write_yaml(doc)
    try:
        issues = _compare_one("svc", rel, doc)
        assert not any("examples" in i for i in issues), \
            f"Unexpected examples issue: {issues}"
    finally:
        Path(rel).unlink(missing_ok=True)
