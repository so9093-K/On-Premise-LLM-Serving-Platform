from __future__ import annotations

import pytest

from ai_model_serving.contracts.chat_response_format import _validate_response_format
from ai_model_serving.contracts.common import reject_unknown_fields
from ai_model_serving.contracts.media import _validate_input_audio
from ai_model_serving.errors import ServiceError, default_code_for_status, error_payload

# Permissive policy so the response_format validator reaches a field-level rejection
# rather than the "not enabled" gate (either way param must name response_format).
_RF_POLICY = {"response_format": {"enabled": True, "types": ["text", "json_schema"]}}


def _raise(fn) -> ServiceError:
    with pytest.raises(ServiceError) as excinfo:
        fn()
    return excinfo.value


def test_response_format_error_carries_response_format_param():
    exc = _raise(lambda: _validate_response_format({"type": "bogus"}, {"messages": []}, _RF_POLICY))
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param is not None and exc.param.split(".")[0] == "response_format"


def test_input_audio_error_carries_input_audio_param():
    exc = _raise(
        lambda: _validate_input_audio(
            {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "xyz"}},
            allowed_audio_formats={"wav"},
            max_audio_bytes=1000,
        )
    )
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param == "input_audio"


def test_wrong_response_format_and_wrong_data_format_are_distinguishable():
    """The reported feedback: a client could not tell a wrong output spec from a wrong
    input data format because both are VALIDATION_ERROR. error.param now separates them."""
    rf = _raise(lambda: _validate_response_format({"type": "bogus"}, {"messages": []}, _RF_POLICY))
    df = _raise(
        lambda: _validate_input_audio(
            {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "xyz"}},
            allowed_audio_formats={"wav"},
            max_audio_bytes=1000,
        )
    )
    assert rf.param.split(".")[0] == "response_format"
    assert df.param.split(".")[0] == "input_audio"
    assert rf.param != df.param


def test_reject_unknown_fields_sets_param_from_context():
    exc = _raise(lambda: reject_unknown_fields({"junk": 1}, {"data"}, "input_audio"))
    assert exc.param == "input_audio"


def test_default_code_for_status_does_not_contradict_status():
    # Previously every non-401 HTTPException collapsed to VALIDATION_ERROR (a 404 body
    # said code=VALIDATION_ERROR). The code must now match the status.
    assert default_code_for_status(401) == "UNAUTHORIZED"
    assert default_code_for_status(403) == "FORBIDDEN"
    assert default_code_for_status(404) == "NOT_FOUND"
    assert default_code_for_status(413) == "REQUEST_TOO_LARGE"
    assert default_code_for_status(503) == "MODEL_UNAVAILABLE"
    assert default_code_for_status(504) == "UPSTREAM_TIMEOUT"
    assert default_code_for_status(404) != "VALIDATION_ERROR"


def test_status_default_code_is_consistent_with_error_status():
    # Every status->code default must round-trip: ERROR_STATUS[code] == status. Otherwise
    # a HTTPException would carry a code whose own canonical status differs from the one
    # actually returned.
    from ai_model_serving.errors import ERROR_STATUS, STATUS_DEFAULT_CODE

    for status, code in STATUS_DEFAULT_CODE.items():
        assert ERROR_STATUS.get(code) == status, f"{status} -> {code} but ERROR_STATUS[{code}]={ERROR_STATUS.get(code)}"


def test_platform_envelope_http_statuses_have_non_contradictory_default():
    # Only apps that install the platform error handlers (gateway, risk-adapter, and
    # their routers) map HTTPException via default_code_for_status. apps/admin_sidecar.py
    # is an internal service-token app on a plain FastAPI() with the default {"detail"}
    # shape, so its statuses (e.g. a legitimate 409 eviction conflict) are out of scope.
    import re
    from pathlib import Path

    from ai_model_serving.errors import ERROR_STATUS, default_code_for_status

    root = Path(__file__).resolve().parents[2] / "src" / "ai_model_serving"
    statuses = set()
    for path in root.rglob("*.py"):
        if path.name == "admin_sidecar.py":
            continue
        for match in re.finditer(r"HTTPException\(\s*(\d{3})", path.read_text(encoding="utf-8")):
            statuses.add(int(match.group(1)))
    for status in statuses:
        code = default_code_for_status(status)
        assert ERROR_STATUS.get(code) == status, (
            f"HTTPException({status}) maps to code {code} whose status is {ERROR_STATUS.get(code)}; "
            "add the status to STATUS_DEFAULT_CODE or use a status with a matching platform code"
        )


def test_error_payload_omits_param_when_absent_and_includes_when_present():
    without = error_payload("INTERNAL_ERROR", "x", False)["error"]
    assert "param" not in without
    with_param = error_payload("VALIDATION_ERROR", "x", False, param="input_audio.format")["error"]
    assert with_param["param"] == "input_audio.format"
