from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from ai_model_serving.auth_control import auth_status_document, diagnose_auth
from ai_model_serving.settings import AppSettings, RuntimeEndpoint, SecuritySettings, load_settings


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


def test_auth_status_reads_exposure_from_explicit_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / "candidate.env"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "AUTH_MODE=local_open",
                "API_KEY_REQUIRED=false",
                "ADMIN_API_KEY_REQUIRED=false",
                "ADMIN_ENDPOINTS_INTERNAL_ONLY=false",
                "INTERNAL_SERVICE_AUTH_REQUIRED=false",
                "FASTAPI_DOCS_ENABLED=true",
                "EXPOSURE_MODE=master_open",
                "EXPOSURE_AUDIENCE=private_lan",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPOSURE_MODE", raising=False)
    monkeypatch.delenv("EXPOSURE_AUDIENCE", raising=False)

    settings = load_settings(env_file=env_path)
    doc = auth_status_document(settings, Path.cwd(), env_path)

    assert doc["exposure_mode"] == "master_open"
    assert doc["canonical_exposure_mode"] == "master_open"


def test_auth_status_cli_reads_exposure_from_env_file(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    env_path = tmp_path / "candidate.env"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "AUTH_MODE=local_open",
                "API_KEY_REQUIRED=false",
                "ADMIN_API_KEY_REQUIRED=false",
                "ADMIN_ENDPOINTS_INTERNAL_ONLY=false",
                "INTERNAL_SERVICE_AUTH_REQUIRED=false",
                "FASTAPI_DOCS_ENABLED=true",
                "EXPOSURE_MODE=master_open",
                "EXPOSURE_AUDIENCE=private_lan",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPOSURE_MODE", raising=False)
    monkeypatch.delenv("EXPOSURE_AUDIENCE", raising=False)

    result = subprocess.run(
        [sys.executable, "scripts/auth/auth_status.py", "--env", str(env_path), "--json"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["exposure_mode"] == "master_open"
    assert payload["canonical_exposure_mode"] == "master_open"


def test_auth_doctor_reads_internal_trusted_evidence_from_explicit_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / "trusted.env"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "AUTH_MODE=internal_trusted",
                "API_KEY_REQUIRED=false",
                "ADMIN_API_KEY_REQUIRED=false",
                "ADMIN_ENDPOINTS_INTERNAL_ONLY=true",
                "INTERNAL_SERVICE_AUTH_REQUIRED=false",
                "FASTAPI_DOCS_ENABLED=false",
                "API_KEY=api-key",
                "API_KEYS=api-key",
                "ADMIN_API_KEY=admin-key",
                "ADMIN_API_KEYS=admin-key",
                "INTERNAL_SERVICE_TOKEN=internal-key",
                "INTERNAL_TRUSTED_AUTH_EVIDENCE=edge gateway authenticates callers; CHG-1234",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXPOSURE_MODE", "private_network")

    settings = load_settings(env_file=env_path)
    findings = diagnose_auth(settings, Path.cwd())

    assert not any(f.code == "INTERNAL_TRUSTED_EVIDENCE_MISSING" for f in findings)


def test_auth_doctor_reads_custom_risk_acceptance_from_explicit_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / "custom.env"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "AUTH_MODE=custom",
                "API_KEY_REQUIRED=true",
                "ADMIN_API_KEY_REQUIRED=true",
                "ADMIN_ENDPOINTS_INTERNAL_ONLY=false",
                "INTERNAL_SERVICE_AUTH_REQUIRED=true",
                "FASTAPI_DOCS_ENABLED=false",
                "API_KEY=api-key",
                "API_KEYS=api-key",
                "ADMIN_API_KEY=admin-key",
                "ADMIN_API_KEYS=admin-key",
                "INTERNAL_SERVICE_TOKEN=internal-key",
                "CUSTOM_AUTH_RISK_ACCEPTED=true",
                "CUSTOM_AUTH_RISK_TICKET=CHG-9999",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXPOSURE_MODE", "private_network")

    settings = load_settings(env_file=env_path)
    findings = diagnose_auth(settings, Path.cwd())

    assert not any(f.code == "CUSTOM_AUTH_RISK_ACCEPTANCE_REQUIRED" for f in findings)
