"""공개 오류 code가 endpoint의 실제 책임과 인증 경계를 따르는지 검증한다.

Gateway의 upstream-backed Chat과 Risk Adapter의 local detector는 같은 POST이지만
발생 가능한 오류가 다르다. strict 문서의 readiness는 실제 admin security에
맞게 공통 401 envelope를 공개해야 한다.
"""

from __future__ import annotations

from typing import Any

from scripts.openapi_assets import build_generated_openapi


def _error_codes(response: dict[str, Any]) -> set[str]:
    schema = response["content"]["application/json"]["schema"]
    return set(schema["properties"]["error"]["properties"]["code"]["enum"])


def test_generated_openapi_scopes_errors_by_endpoint_and_effective_auth():
    documents, _ = build_generated_openapi()

    chat = documents["gateway"]["paths"]["/v1/chat/completions"]["post"]["responses"]
    assert _error_codes(chat["503"]) == {
        "CIRCUIT_OPEN",
        "MAIN_MODEL_CONTROL_UNAVAILABLE",
        "MAIN_MODEL_SWITCH_IN_PROGRESS",
        "MODEL_UNAVAILABLE",
        "QUEUE_TIMEOUT",
    }

    local_pii = documents["risk-adapter"]["paths"][
        "/v1/risk/detectors/pii/assessments"
    ]["post"]["responses"]
    assert not {"429", "502", "503", "504"} & set(local_pii)

    for document in documents.values():
        readiness = document["paths"]["/ready"]["get"]
        assert readiness["security"] == [{"adminBearerAuth": []}]
        assert _error_codes(readiness["responses"]["401"]) == {"UNAUTHORIZED"}
