"""응답이 공개 계약을 넘어서지 않는지 고정한다.

런타임이 붙인 것이 그대로 나가면 /docs가 보여주는 모양과 실제 응답이 다르다.
실측에서 mlx-vlm은 chunk마다 timings(peak_memory, draft_kind, draft_rounds)를
실었고 payload의 25%가 그 내부 상태였다. message와 delta에는 OpenAI가 요청
쪽에서만 쓰는 tool_call_id와 name이 null로 있었다.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from ai_model_serving.contracts.chat_response import (
    project_stream_chunk,
    project_to_public_contract,
    validate_chat_response,
)

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads(
    (ROOT / "specs/schemas/chat_completion_response.schema.json").read_text(encoding="utf-8")
)


def _runtime_response() -> dict:
    """mlx-vlm이 실제로 돌려준 모양."""
    return {
        "id": "chatcmpl-x", "object": "chat.completion", "created": 1, "model": "local-main",
        "usage": {"prompt_tokens": 25, "completion_tokens": 11, "total_tokens": 36,
                  "prompt_tokens_details": {"cached_tokens": 0}},
        "timings": {"predicted_n": 11, "peak_memory": 16.0, "draft_kind": "mtp",
                    "draft_rounds": 4, "draft_n_accepted": 8},
        "choices": [{
            "index": 0, "finish_reason": "stop", "logprobs": None,
            "message": {"role": "assistant", "content": "안녕하세요", "reasoning_content": None,
                        "reasoning": None, "tool_calls": None, "tool_call_id": None, "name": None},
        }],
    }


def test_runtime_internals_do_not_reach_the_client():
    projected = project_to_public_contract(_runtime_response())
    assert "timings" not in projected
    message = projected["choices"][0]["message"]
    assert "tool_call_id" not in message and "name" not in message


def test_the_allowed_keys_come_from_the_schema_not_from_a_list_in_code():
    """코드에 목록을 다시 적으면 schema와 갈라지고, 갈라진 쪽이 실제 동작이 된다.

    schema가 세 레벨 모두 additionalProperties=false를 선언하므로, 좁히기가 놓친
    키는 여기서 schema 위반으로 잡힌다 -- 허용 키 목록을 테스트에 옮겨 적지 않는다.
    """
    Draft202012Validator(SCHEMA).validate(project_to_public_contract(_runtime_response()))


def test_declared_extensions_survive():
    """reasoning은 이 플랫폼이 광고하는 확장이다. 좁히기가 그것까지 지우면 안 된다."""
    payload = _runtime_response()
    payload["choices"][0]["message"]["reasoning"] = "thinking"
    payload["choices"][0]["message"]["reasoning_content"] = "thinking"
    projected = project_to_public_contract(payload)
    assert projected["choices"][0]["message"]["reasoning"] == "thinking"
    assert projected["choices"][0]["message"]["reasoning_content"] == "thinking"


def test_tool_calls_survive_when_present():
    payload = _runtime_response()
    calls = [{"id": "call_1", "type": "function",
              "function": {"name": "get_weather", "arguments": "{}"}}]
    payload["choices"][0]["message"]["tool_calls"] = calls
    payload["choices"][0]["finish_reason"] = "tool_calls"
    payload["choices"][0]["message"]["content"] = None
    assert project_to_public_contract(payload)["choices"][0]["message"]["tool_calls"] == calls


def test_usage_keeps_the_standard_fields():
    usage = project_to_public_contract(_runtime_response())["usage"]
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert field in usage
    # 선언이 비어 있으면 /docs에 빈 객체로 보인다. 실제로 오는 것을 선언한다.
    declared = SCHEMA["properties"]["usage"]["properties"]
    assert set(usage) <= set(declared)


def test_streaming_chunks_are_narrowed_the_same_way():
    """비스트리밍만 좁히면 두 경로가 다른 계약을 내보낸다."""
    chunk = {
        "id": "chatcmpl-x", "object": "chat.completion.chunk", "created": 1, "model": "local-main",
        "timings": {"peak_memory": 16.0, "draft_kind": "mtp"},
        "choices": [{"index": 0, "finish_reason": None,
                     "delta": {"role": "assistant", "content": "안", "name": None,
                               "tool_call_id": None}}],
    }
    projected = project_stream_chunk(chunk)
    assert "timings" not in projected
    assert projected["object"] == "chat.completion.chunk"
    delta = projected["choices"][0]["delta"]
    assert set(delta) == {"role", "content"}


def test_validate_chat_response_returns_the_narrowed_document():
    """검증만 하고 원본을 돌려주면 좁히기가 적용되지 않는다."""
    returned = validate_chat_response(_runtime_response(), expected_model="local-main")
    assert "timings" not in returned
