from __future__ import annotations

from pathlib import Path

from ai_model_serving.auth_control import auth_status_document, diagnose_auth
from ai_model_serving.settings import AppSettings, RuntimeEndpoint, SecuritySettings


def _settings(**security_overrides):
    endpoint = RuntimeEndpoint("x", "http://runtime/v1", "x", 1)
    security = {
        "api_key_required": True,
        "api_keys": frozenset({"api-key"}),
        "internal_service_token": "internal-key",
        "internal_service_auth_required": True,
        "auth_mode": "strict",
        "admin_api_key_required": True,
        "admin_api_keys": frozenset({"admin-key"}),
        "admin_endpoints_internal_only": False,
    }
    security.update(security_overrides)
    return AppSettings(
        app_env="staging",
        project_version="0.1.0",
        security=SecuritySettings(**security),
        gateway_timeout_seconds=1,
        risk_adapter_timeout_seconds=1,
        main_llm=endpoint,
        embedding=endpoint,
        risk_prompt=endpoint,
        risk_adapter_base_url="http://risk",
    )


def test_auth_status_reports_internal_service_axis():
    doc = auth_status_document(_settings(api_key_required=False), Path.cwd())
    assert doc["public_api"]["/v1/*"] == "unauthenticated"
    assert doc["internal_services"]["gateway_to_risk_adapter"] == "internal_token_required"


def test_auth_doctor_flags_disabled_internal_auth_in_non_local_env():
    findings = diagnose_auth(
        _settings(internal_service_auth_required=False, auth_mode="custom"),
        Path.cwd(),
    )
    assert any(f.code == "INTERNAL_SERVICE_AUTH_DISABLED_NON_LOCAL" and f.level == "FAIL" for f in findings)


def test_auth_profile_env_values_match_managed_modes():
    from ai_model_serving.auth_control import auth_profile_env_values

    assert auth_profile_env_values("local_open") == {
        "AUTH_MODE": "local_open",
        "API_KEY_REQUIRED": "false",
        "ADMIN_API_KEY_REQUIRED": "false",
        "ADMIN_ENDPOINTS_INTERNAL_ONLY": "false",
        "INTERNAL_SERVICE_AUTH_REQUIRED": "false",
        "FASTAPI_DOCS_ENABLED": "true",
    }
    assert auth_profile_env_values("private_network") == {
        "AUTH_MODE": "private_network",
        "API_KEY_REQUIRED": "true",
        "ADMIN_API_KEY_REQUIRED": "true",
        "ADMIN_ENDPOINTS_INTERNAL_ONLY": "false",
        "INTERNAL_SERVICE_AUTH_REQUIRED": "true",
        "FASTAPI_DOCS_ENABLED": "true",
    }


def test_auth_doctor_accepts_private_network_profile_flags():
    findings = diagnose_auth(
        _settings(
            auth_mode="private_network",
            api_key_required=True,
            admin_api_key_required=True,
            internal_service_auth_required=True,
            admin_endpoints_internal_only=False,
        ),
        Path("/tmp/nonexistent-auth-project-root"),
    )
    assert not any(f.code == "AUTH_MODE_FLAG_MISMATCH" for f in findings)
    assert not any(f.level == "FAIL" for f in findings)


def test_auth_status_document_records_explicit_env_path(tmp_path):
    env_path = tmp_path / "candidate.env"
    env_path.write_text("APP_ENV=local\n", encoding="utf-8")
    doc = auth_status_document(_settings(api_key_required=False), Path.cwd(), env_path)
    assert doc["env_file"]["path"] == str(env_path)
    assert doc["env_file"]["exists"] is True
    assert doc["env_file"]["repository_default"] is False
