"""docs/operations/endpoint_reference.md에 박제된 생성 엔드포인트 매트릭스가
실제 EndpointSpec 레지스트리와 최신 상태로 일치하는지 검증한다."""

from __future__ import annotations

from pathlib import Path

from ai_model_serving.api.endpoint_matrix import BEGIN_MARKER, END_MARKER, generate_block

_ROOT = Path(__file__).parent.parent.parent
_DOC_PATH = _ROOT / "docs" / "operations" / "endpoint_reference.md"


def _extract_block(text: str) -> str:
    begin_idx = text.find(BEGIN_MARKER)
    end_idx = text.find(END_MARKER)
    assert begin_idx != -1 and end_idx != -1, (
        f"Markers not found in {_DOC_PATH}. "
        f"Expected {BEGIN_MARKER!r} and {END_MARKER!r}."
    )
    return text[begin_idx : end_idx + len(END_MARKER)]


def test_endpoint_matrix_block_is_current() -> None:
    """endpoint_reference.md의 생성된 endpoint matrix는 registry와 일치해야 한다.

    수정법: python3 scripts/generate_endpoint_matrix.py --update
    """
    current_text = _DOC_PATH.read_text(encoding="utf-8")
    current_block = _extract_block(current_text)
    expected_block = generate_block()

    assert current_block == expected_block, (
        "Endpoint matrix block is out of date.\n"
        "Run: python3.12 scripts/generate_endpoint_matrix.py --update"
    )
