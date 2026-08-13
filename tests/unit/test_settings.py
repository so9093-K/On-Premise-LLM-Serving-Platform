"""load_settings()가 env/.env/config yaml을 올바른 우선순위로 합치고, 운영
환경(non-local)에서 위험한 기본값(플레이스홀더 secret, 내부 토큰 없음, admin
key 없음 등)을 거부하는지 검증한다."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest
import yaml

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
    monkeypatch.setenv("API_KEY_REQUIRED", "true")
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
    monkeypatch.setenv("API_KEY_REQUIRED", "true")
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
    root = tmp_path
    (root / "configs").mkdir()
    repo = Path(__file__).resolve().parents[2]
    shutil.copy(repo / "configs" / "model_serving.yaml", root / "configs" / "model_serving.yaml")
    shutil.copy(repo / "configs" / "model_catalog.yaml", root / "configs" / "model_catalog.yaml")
    shutil.copy(repo / "configs" / "main_model_profiles.yaml", root / "configs" / "main_model_profiles.yaml")
    shutil.copy(repo / "configs" / "main_model_profiles.yaml", root / "configs" / "main_model_profiles.yaml")
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
    catalog = yaml.safe_load((root / "configs" / "model_catalog.yaml").read_text(encoding="utf-8"))
    expected_public_ids = {
        model_id
        for model_id, metadata in catalog["models"].items()
        if metadata.get("gateway_listing", {}).get("enabled", True) is True
    }
    assert {item["id"] for item in settings.public_models} == expected_public_ids

    monkeypatch.setenv("API_KEYS", "exported-key")
    settings = load_settings(root)
    assert settings.security.api_keys == frozenset({"exported-key"})



def test_load_settings_ignores_local_dotenv_when_app_env_is_explicitly_non_local(tmp_path, monkeypatch):
    root = tmp_path
    (root / "configs").mkdir()
    repo = Path(__file__).resolve().parents[2]
    shutil.copy(repo / "configs" / "model_serving.yaml", root / "configs" / "model_serving.yaml")
    shutil.copy(repo / "configs" / "model_catalog.yaml", root / "configs" / "model_catalog.yaml")
    shutil.copy(repo / "configs" / "main_model_profiles.yaml", root / "configs" / "main_model_profiles.yaml")
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
    monkeypatch.setenv("API_KEY_REQUIRED", "true")
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


def test_load_settings_uses_serving_runtime_defaults(tmp_path):
    from pathlib import Path
    import shutil
    import yaml

    root = tmp_path
    (root / "configs").mkdir()
    repo = Path(__file__).resolve().parents[2]
    shutil.copy(repo / "configs" / "model_serving.yaml", root / "configs" / "model_serving.yaml")
    shutil.copy(repo / "configs" / "model_catalog.yaml", root / "configs" / "model_catalog.yaml")
    shutil.copy(repo / "configs" / "main_model_profiles.yaml", root / "configs" / "main_model_profiles.yaml")
    shutil.copy(repo / "VERSION", root / "VERSION")

    serving_path = root / "configs" / "model_serving.yaml"
    serving = yaml.safe_load(serving_path.read_text(encoding="utf-8"))
    profiles = yaml.safe_load((root / "configs" / "main_model_profiles.yaml").read_text(encoding="utf-8"))
    default_policy = profiles["profiles"][profiles["default_profile"]]["gateway_policy"]
    main_llm = serving["models"]["main_llm"]
    embedding = serving["models"]["embedding"]
    main_admission = main_llm["resource_control"]["admission_control"]
    embedding_admission = embedding["resource_control"]["admission_control"]

    settings = load_settings(root)
    assert settings.default_main_model_gateway_policy == default_policy
    assert settings.main_llm.max_concurrency == main_admission["max_concurrency"]
    assert settings.main_llm.queue_timeout_seconds == main_admission["queue_timeout_seconds"]
    assert settings.embedding.max_concurrency == embedding_admission["max_concurrency"]
    assert settings.embedding.queue_timeout_seconds == embedding_admission["queue_timeout_seconds"]

    main_admission["max_concurrency"] += 1
    main_admission["queue_timeout_seconds"] += 1
    embedding_admission["max_concurrency"] += 1
    embedding_admission["queue_timeout_seconds"] += 1
    serving_path.write_text(yaml.safe_dump(serving, allow_unicode=True), encoding="utf-8")

    settings = load_settings(root)
    assert settings.default_main_model_gateway_policy == default_policy
    assert settings.main_llm.max_concurrency == main_admission["max_concurrency"]
    assert settings.main_llm.queue_timeout_seconds == main_admission["queue_timeout_seconds"]
    assert settings.embedding.max_concurrency == embedding_admission["max_concurrency"]
    assert settings.embedding.queue_timeout_seconds == embedding_admission["queue_timeout_seconds"]


def test_load_settings_rejects_invalid_or_missing_required_model_configuration(tmp_path):
    from pathlib import Path
    import shutil
    import yaml

    root = tmp_path
    (root / "configs").mkdir()
    repo = Path(__file__).resolve().parents[2]
    shutil.copy(repo / "configs" / "model_serving.yaml", root / "configs" / "model_serving.yaml")
    shutil.copy(repo / "configs" / "model_catalog.yaml", root / "configs" / "model_catalog.yaml")
    shutil.copy(repo / "configs" / "main_model_profiles.yaml", root / "configs" / "main_model_profiles.yaml")
    shutil.copy(repo / "VERSION", root / "VERSION")

    serving_path = root / "configs" / "model_serving.yaml"
    serving = yaml.safe_load(serving_path.read_text(encoding="utf-8"))
    serving["embedding_profiles"]["local-embed"]["prompt_policy"]["retrieval_query"]["mode"] = (
        "sentence_transformers_prompt_name"
    )
    serving_path.write_text(yaml.safe_dump(serving, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RuntimeError, match="must be 'none' or 'prefix'"):
        load_settings(root)

    serving = yaml.safe_load((repo / "configs" / "model_serving.yaml").read_text(encoding="utf-8"))
    serving.pop("embedding_profiles")
    serving_path.write_text(yaml.safe_dump(serving, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="embedding_profiles must be a non-empty mapping"):
        load_settings(root)

    serving = yaml.safe_load((repo / "configs" / "model_serving.yaml").read_text(encoding="utf-8"))
    serving["risk_adapter"].pop("detectors")
    serving_path.write_text(yaml.safe_dump(serving, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="risk_adapter.detectors must be a non-empty mapping"):
        load_settings(root)


def test_load_settings_requires_internal_token_in_non_local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY_REQUIRED", "true")
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
    monkeypatch.setenv("API_KEY_REQUIRED", "true")
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
        "default_embedding_model": "local-embed",
        "default_retrieval_model": "local-embed",
    }


def test_app_settings_requires_explicit_embedding_configuration() -> None:
    kwargs = _minimal_settings_kwargs()
    kwargs.pop("default_embedding_model")
    kwargs.pop("default_retrieval_model")
    with pytest.raises(ValueError, match="embedding_profiles must be explicitly configured"):
        AppSettings(**kwargs)


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
            **{
                **_minimal_settings_kwargs(),
                "default_embedding_model": "local-embed",
                "default_retrieval_model": "missing",
            },
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
        )


def test_load_settings_can_disable_fastapi_docs_explicitly(monkeypatch):
    monkeypatch.setenv("FASTAPI_DOCS_ENABLED", "false")
    settings = load_settings()
    assert settings.documentation.enabled is False


def test_load_settings_log_request_response_body_defaults_to_false(monkeypatch):
    monkeypatch.delenv("LOG_REQUEST_RESPONSE_BODY", raising=False)
    settings = load_settings()
    assert settings.log_request_response_body is False


def test_load_settings_can_enable_log_request_response_body(monkeypatch):
    monkeypatch.setenv("LOG_REQUEST_RESPONSE_BODY", "true")
    settings = load_settings()
    assert settings.log_request_response_body is True


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
    # MAX_REQUEST_BODY_BYTES는 yaml이 소유한다(operational_limits.max_request_body_bytes).
    # env 변수가 없을 때(isolate fixture가 지워버림), settings는 yaml 값을 읽어야
    # 배포된 body 상한을 단일 source of truth가 결정하게 된다.
    from pathlib import Path

    import yaml

    cfg_path = Path(__file__).resolve().parents[2] / "configs" / "model_serving.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    expected = int(cfg["operational_limits"]["max_request_body_bytes"])

    settings = load_settings()
    assert settings.max_request_body_bytes == expected
