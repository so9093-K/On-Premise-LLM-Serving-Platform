from __future__ import annotations

import pytest

from ai_model_serving.settings import AppSettings, EmbeddingProfile, RuntimeEndpoint, SecuritySettings, load_settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch):
    for name in [
        "APP_ENV",
        "AUTH_MODE",
        "API_KEY_REQUIRED",
        "API_KEYS",
        "INTERNAL_SERVICE_TOKEN",
        "INTERNAL_SERVICE_AUTH_REQUIRED",
        "ADMIN_API_KEY_REQUIRED",
        "ADMIN_API_KEY",
        "ADMIN_API_KEYS",
        "ADMIN_ENDPOINTS_INTERNAL_ONLY",
        "FASTAPI_DOCS_ENABLED",
        "FASTAPI_DOCS_URL",
        "FASTAPI_REDOC_URL",
        "OPENAPI_URL",
        "MAX_REQUEST_BODY_BYTES",
        "MAIN_LLM_MAX_CONCURRENCY",
        "MAIN_LLM_QUEUE_TIMEOUT_SECONDS",
        "EMBEDDING_MAX_CONCURRENCY",
        "EMBEDDING_QUEUE_TIMEOUT_SECONDS",
        "RISK_PROMPT_TIMEOUT_SECONDS",
        "RISK_SIREN_TIMEOUT_SECONDS",
        "RISK_ADAPTER_TIMEOUT_SECONDS",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_load_settings_rejects_default_api_key_in_non_local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY_REQUIRED", "true")
    monkeypatch.setenv("INTERNAL_SERVICE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "real-internal-token")
    monkeypatch.setenv("API_KEYS", "change-me")
    with pytest.raises(RuntimeError, match="API_KEYS must be set"):
        load_settings()


def test_load_settings_allows_non_default_api_key_in_non_local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEYS", "real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-real-key")
    monkeypatch.setenv("ADMIN_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-real-key")
    settings = load_settings()
    assert settings.security.api_keys == frozenset({"real-key"})
    assert settings.security.admin_api_key_required is True
    assert settings.security.admin_api_keys == frozenset({"admin-real-key"})


def test_load_settings_warns_on_open_admin_endpoints_in_non_local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEYS", "real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-real-key")
    monkeypatch.setenv("ADMIN_API_KEY_REQUIRED", "false")
    monkeypatch.setenv("ADMIN_ENDPOINTS_INTERNAL_ONLY", "false")
    with pytest.warns(UserWarning, match="ADMIN_API_KEY_REQUIRED=false"):
        settings = load_settings()
    assert settings.security.admin_api_key_required is False

    monkeypatch.setenv("ADMIN_ENDPOINTS_INTERNAL_ONLY", "true")
    settings = load_settings()
    assert settings.security.admin_api_key_required is False
    assert settings.security.admin_endpoints_internal_only is True


def test_load_settings_reads_local_dotenv_without_overriding_exported_values(tmp_path, monkeypatch):
    from pathlib import Path
    import shutil

    root = tmp_path
    (root / "configs").mkdir()
    repo = Path(__file__).resolve().parents[2]
    shutil.copy(repo / "configs" / "model_serving.yaml", root / "configs" / "model_serving.yaml")
    shutil.copy(repo / "configs" / "model_catalog.yaml", root / "configs" / "model_catalog.yaml")
    shutil.copy(repo / "VERSION", root / "VERSION")
    (root / ".env").write_text(
        "APP_ENV=local\nAPI_KEYS=dotenv-key\nMAX_REQUEST_BODY_BYTES=1234\nMAIN_LLM_MAX_CONCURRENCY=2\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    monkeypatch.delenv("MAIN_LLM_MAX_CONCURRENCY", raising=False)
    settings = load_settings(root)
    assert settings.security.api_keys == frozenset({"dotenv-key"})
    assert settings.max_request_body_bytes == 1234
    assert settings.main_llm.max_concurrency == 2
    assert {item["id"] for item in settings.public_models} == {"local-main", "local-embed", "local-embed-ko", "risk-prompt"}

    monkeypatch.setenv("API_KEYS", "exported-key")
    settings = load_settings(root)
    assert settings.security.api_keys == frozenset({"exported-key"})



def test_load_settings_ignores_local_dotenv_when_app_env_is_explicitly_non_local(tmp_path, monkeypatch):
    from pathlib import Path
    import shutil

    root = tmp_path
    (root / "configs").mkdir()
    repo = Path(__file__).resolve().parents[2]
    shutil.copy(repo / "configs" / "model_serving.yaml", root / "configs" / "model_serving.yaml")
    shutil.copy(repo / "configs" / "model_catalog.yaml", root / "configs" / "model_catalog.yaml")
    shutil.copy(repo / "VERSION", root / "VERSION")
    (root / ".env").write_text(
        "APP_ENV=local\n"
        "API_KEYS=local-generated-key\n"
        "INTERNAL_SERVICE_TOKEN=local-internal-token\n"
        "ADMIN_API_KEY_REQUIRED=true\n"
        "ADMIN_API_KEYS=local-admin-key\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEYS", "real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("ADMIN_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-real-key")
    monkeypatch.delenv("ADMIN_API_KEYS", raising=False)

    settings = load_settings(root)
    assert settings.app_env == "production"
    assert settings.security.api_keys == frozenset({"real-key"})
    assert settings.security.internal_service_token == "internal-real-key"
    assert settings.security.internal_service_auth_required is True
    assert settings.security.admin_api_keys == frozenset({"admin-real-key"})


def test_load_settings_requires_internal_token_in_non_local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEYS", "real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_AUTH_REQUIRED", "true")
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="INTERNAL_SERVICE_TOKEN"):
        load_settings()


def test_load_settings_warns_on_disabled_api_key_in_non_local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY_REQUIRED", "false")
    monkeypatch.setenv("API_KEYS", "real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-real-key")
    monkeypatch.setenv("ADMIN_API_KEY_REQUIRED", "false")
    monkeypatch.setenv("ADMIN_ENDPOINTS_INTERNAL_ONLY", "true")
    with pytest.warns(UserWarning, match="API_KEY_REQUIRED=false"):
        settings = load_settings()
    assert settings.security.api_key_required is False



def test_load_settings_supports_independent_internal_service_auth_flag(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("API_KEY_REQUIRED", "false")
    monkeypatch.setenv("INTERNAL_SERVICE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-local-key")
    settings = load_settings()
    assert settings.security.api_key_required is False
    assert settings.security.internal_service_auth_required is True


def test_load_settings_rejects_risk_adapter_timeout_below_sequential_budget(monkeypatch):
    monkeypatch.setenv("RISK_ADAPTER_TIMEOUT_SECONDS", "6")
    with pytest.raises(RuntimeError, match="RISK_ADAPTER_TIMEOUT_SECONDS"):
        load_settings()


def test_load_settings_supports_per_model_timeout_overrides(monkeypatch):
    monkeypatch.setenv("RISK_PROMPT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("RISK_ADAPTER_TIMEOUT_SECONDS", "10")
    settings = load_settings()
    assert settings.risk_prompt.timeout_seconds == 3
    assert "risk_siren" not in settings.runtime_endpoints


def test_load_settings_uses_nested_admission_control_defaults(monkeypatch):
    for name in [
        "MAIN_LLM_MAX_CONCURRENCY",
        "MAIN_LLM_QUEUE_TIMEOUT_SECONDS",
        "EMBEDDING_MAX_CONCURRENCY",
        "EMBEDDING_QUEUE_TIMEOUT_SECONDS",
    ]:
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert settings.main_llm.max_concurrency == 1
    assert settings.main_llm.queue_timeout_seconds == 2
    assert settings.embedding.max_concurrency == 2
    assert settings.embedding.queue_timeout_seconds == 2


def test_load_settings_rejects_generated_placeholder_secrets_in_non_local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY_REQUIRED", "true")
    monkeypatch.setenv("INTERNAL_SERVICE_AUTH_REQUIRED", "true")
    monkeypatch.setenv("API_KEYS", "generate-with-make-init-env")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-real-key")
    monkeypatch.setenv("ADMIN_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "admin-real-key")
    with pytest.raises(RuntimeError, match="API_KEYS must be set"):
        load_settings()

    monkeypatch.setenv("API_KEYS", "real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "replace-me")
    with pytest.raises(RuntimeError, match="INTERNAL_SERVICE_TOKEN"):
        load_settings()


def test_load_settings_requires_admin_key_when_admin_auth_enabled(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEYS", "real-key")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-real-key")
    monkeypatch.setenv("ADMIN_API_KEY_REQUIRED", "true")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("ADMIN_API_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
        load_settings()

    monkeypatch.setenv("ADMIN_API_KEYS", "admin-one,admin-two")
    settings = load_settings()
    assert settings.security.admin_api_keys == frozenset({"admin-one", "admin-two"})


def test_load_settings_enables_fastapi_docs_by_default(monkeypatch):
    monkeypatch.delenv("FASTAPI_DOCS_ENABLED", raising=False)
    settings = load_settings()
    assert settings.documentation.enabled is True
    assert settings.documentation.docs_url == "/docs"
    assert settings.documentation.redoc_url == "/redoc"
    assert settings.documentation.openapi_url == "/openapi.json"


def _minimal_settings_kwargs() -> dict:
    endpoint = RuntimeEndpoint("local-embed", "http://embed/v1", "local-embed", 1)
    return {
        "app_env": "test",
        "project_version": "0.1.0",
        "security": SecuritySettings(
            api_key_required=False,
            api_keys=frozenset(),
            internal_service_token="internal",
        ),
        "gateway_timeout_seconds": 1,
        "risk_adapter_timeout_seconds": 1,
        "risk_adapter_base_url": "http://risk",
        "runtime_endpoints": {"embedding": endpoint},
        "embedding": endpoint,
    }


def test_app_settings_validates_embedding_route_service_keys() -> None:
    with pytest.raises(ValueError, match="unknown runtime service"):
        AppSettings(
            **_minimal_settings_kwargs(),
            embedding_profiles={
                "local-embed": EmbeddingProfile(
                    model="local-embed",
                    service_key="missing",
                    upstream_model_id="example/embed",
                    dimensions=(768,),
                    default_dimensions=768,
                )
            },
            embedding_model_routes={"local-embed": "missing"},
        )


def test_app_settings_validates_default_embedding_models() -> None:
    with pytest.raises(ValueError, match="default_retrieval_model"):
        AppSettings(
            **_minimal_settings_kwargs(),
            embedding_profiles={
                "local-embed": EmbeddingProfile(
                    model="local-embed",
                    service_key="embedding",
                    upstream_model_id="example/embed",
                    dimensions=(768,),
                    default_dimensions=768,
                )
            },
            embedding_model_routes={"local-embed": "embedding"},
            default_embedding_model="local-embed",
            default_retrieval_model="missing",
        )


def test_load_settings_can_disable_fastapi_docs_explicitly(monkeypatch):
    monkeypatch.setenv("FASTAPI_DOCS_ENABLED", "false")
    settings = load_settings()
    assert settings.documentation.enabled is False


def test_load_settings_can_read_explicit_env_file_outside_repo(tmp_path, monkeypatch):
    env_path = tmp_path / "candidate.env"
    env_path.write_text(
        "APP_ENV=staging\n"
        "AUTH_MODE=private_network\n"
        "API_KEY_REQUIRED=true\n"
        "API_KEYS=explicit-gateway-key\n"
        "ADMIN_API_KEY_REQUIRED=true\n"
        "ADMIN_API_KEYS=explicit-admin-key\n"
        "INTERNAL_SERVICE_AUTH_REQUIRED=true\n"
        "INTERNAL_SERVICE_TOKEN=explicit-internal-key\n"
        "FASTAPI_DOCS_ENABLED=true\n",
        encoding="utf-8",
    )
    settings = load_settings(env_file=env_path)
    assert settings.app_env == "staging"
    assert settings.security.auth_mode == "private_network"
    assert settings.security.api_keys == frozenset({"explicit-gateway-key"})
    assert settings.security.admin_api_keys == frozenset({"explicit-admin-key"})
    assert settings.security.internal_service_token == "explicit-internal-key"


def test_load_settings_uses_yaml_max_request_body_bytes_when_env_unset():
    # MAX_REQUEST_BODY_BYTES is yaml-owned (operational_limits.max_request_body_bytes).
    # With the env var absent (the isolate fixture clears it), settings must read the
    # yaml value so a single source of truth drives the deployed body cap.
    from pathlib import Path

    import yaml

    cfg_path = Path(__file__).resolve().parents[2] / "configs" / "model_serving.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    expected = int(cfg["operational_limits"]["max_request_body_bytes"])

    settings = load_settings()
    assert settings.max_request_body_bytes == expected
