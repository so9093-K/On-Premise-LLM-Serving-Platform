"""공개 계약 스키마에서 OpenAI 호환 범위 문서를 생성한다.

파라미터 목록과 분류는 specs/schemas/*.schema.json이 소유한다
(`x-openai-compatibility`, `x-compatibility-note`, `x-openai-unsupported-parameters`).
표를 문서에 손으로 적으면 스키마가 바뀔 때 문서만 옛 내용을 계속 광고한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "specs" / "schemas"
REQUEST_SCHEMA = "chat_completion_request.schema.json"
RESPONSE_SCHEMA = "chat_completion_response.schema.json"

GENERATED_HEADER = (
    "<!-- 이 파일은 scripts/render_runtime_assets.py가 생성한다. 직접 고치지 않는다.\n"
    "     내용의 출처는 specs/schemas/chat_completion_{request,response}.schema.json이다. -->\n"
)

_CLASS_LABEL = {
    "standard": "표준",
    "narrowed": "표준(좁힘)",
    "extension": "확장",
}
_CLASS_ORDER = ("extension", "narrowed", "standard")


def _load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _constraint(subschema: dict[str, Any]) -> str:
    """스키마가 거는 값 제약을 사람이 읽는 한 줄로 만든다."""
    parts: list[str] = []
    declared = subschema.get("type")
    if isinstance(declared, list):
        parts.append("/".join(declared))
    elif isinstance(declared, str):
        parts.append(declared)
    bounds = [
        ("최소", subschema.get("minimum")),
        ("초과", subschema.get("exclusiveMinimum")),
        ("최대", subschema.get("maximum")),
        ("미만", subschema.get("exclusiveMaximum")),
    ]
    for label, value in bounds:
        if value is not None:
            parts.append(f"{label} {value}")
    if "enum" in subschema:
        parts.append("허용값 " + ", ".join(f"`{item}`" for item in subschema["enum"]))
    return ", ".join(parts) if parts else "-"


def _rows(properties: dict[str, Any], *, context: str) -> list[tuple[str, str, str, str, str]]:
    # 분류가 없는 파라미터를 "표준"으로 기본 처리하면, 새로 추가한 확장이
    # OpenAI 표준인 것처럼 문서에 실린다. 생성기가 만들 수 없는 문서를 만들지
    # 않고 여기서 멈춘다.
    unclassified = sorted(
        name for name, subschema in properties.items()
        if subschema.get("x-openai-compatibility") not in _CLASS_LABEL
    )
    if unclassified:
        raise SystemExit(
            f"{context}: 다음 파라미터에 x-openai-compatibility 선언이 없거나 "
            f"알 수 없는 값이다: {unclassified} "
            f"(허용값: {sorted(_CLASS_LABEL)})"
        )
    rows = []
    for name, subschema in properties.items():
        compatibility = str(subschema["x-openai-compatibility"])
        note = str(
            subschema.get("x-compatibility-note")
            or subschema.get("description")
            or ""
        ).strip().replace("\n", " ")
        rows.append(
            (
                _CLASS_ORDER.index(compatibility),
                name,
                _CLASS_LABEL[compatibility],
                _constraint(subschema),
                note or "-",
            )
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def _table(properties: dict[str, Any], *, required: set[str], context: str) -> str:
    lines = [
        "| 파라미터 | 분류 | 제약 | 비고 |",
        "|---|---|---|---|",
    ]
    for _, name, label, constraint, note in _rows(properties, context=context):
        display = f"`{name}`" + (" (필수)" if name in required else "")
        lines.append(f"| {display} | {label} | {constraint} | {note} |")
    return "\n".join(lines)


def render() -> str:
    request = _load(REQUEST_SCHEMA)
    response = _load(RESPONSE_SCHEMA)
    message = response["properties"]["choices"]["items"]["properties"]["message"]

    counts = {label: 0 for label in _CLASS_ORDER}
    for _, _, label, _, _ in _rows(request["properties"], context=REQUEST_SCHEMA):
        counts[next(key for key, value in _CLASS_LABEL.items() if value == label)] += 1

    sections = [
        GENERATED_HEADER,
        "# OpenAI 호환 범위",
        "",
        "이 Gateway는 OpenAI Chat Completions의 **한정된 부분집합**을 구현하고, 거기에",
        "플랫폼 확장을 더한다. 기존 OpenAI SDK로 `base_url`만 바꿔 호출할 수 있지만",
        "받는 파라미터는 아래 표가 전부다. 선언되지 않은 파라미터는 조용히 무시하지",
        "않고 422로 거부한다 -- 무시하면 사용자가 적용됐다고 믿기 때문이다.",
        "",
        f"요청 파라미터 {len(request['properties'])}개 가운데 "
        f"표준 {counts['standard']}개, 좁힌 표준 {counts['narrowed']}개, "
        f"확장 {counts['extension']}개다.",
        "",
        "## 요청 파라미터",
        "",
        _table(
            request["properties"],
            required=set(request.get("required", [])),
            context=REQUEST_SCHEMA,
        ),
        "",
        "## 거부하는 OpenAI 파라미터",
        "",
        "아래는 OpenAI가 문서화했지만 이 플랫폼이 받지 않는 것들이다. 보내면 422다.",
        "",
        "| 파라미터 | 이유 |",
        "|---|---|",
    ]
    for item in request.get("x-openai-unsupported-parameters", []):
        sections.append(f"| `{item['name']}` | {item['reason']} |")

    sections += [
        "",
        "## 응답 메시지 필드",
        "",
        "응답은 아래 필드로 좁혀서 나간다. runtime이 덧붙인 내부 상태는 전달하지 않는다.",
        "",
        _table(
            message["properties"],
            required=set(message.get("required", [])),
            context=f"{RESPONSE_SCHEMA} choices[].message",
        ),
        "",
    ]
    return "\n".join(sections)


if __name__ == "__main__":
    print(render(), end="")
