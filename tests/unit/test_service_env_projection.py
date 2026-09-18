from __future__ import annotations

from scripts.config.render_service_env import render


def test_static_service_env_projects_legacy_main_model_keys_as_canonical(tmp_path) -> None:
    source = tmp_path / ".env"
    output = tmp_path / "gateway.env"
    source.write_text(
        "APP_ENV=local\n"
        "MAIN_LLM_BASE_URL=http://host.example:9401/v1\n"
        "MAIN_LLM_STATIC_PROFILE=gemma4-12b-unified-fp8\n"
        "MAIN_LLM_MODEL=local-main\n",
        encoding="utf-8",
    )

    name, _ = render(
        target="linux-nvidia-static",
        source_env=source,
        output=output,
    )

    rendered = output.read_text(encoding="utf-8")
    assert name == "static_gateway"
    assert "MAIN_MODEL_BASE_URL=http://host.example:9401/v1" in rendered
    assert "MAIN_MODEL_STATIC_PROFILE=gemma4-12b-unified-fp8" in rendered
    assert "MAIN_MODEL_ALIAS=local-main" in rendered
    assert "MAIN_LLM_" not in rendered


def test_static_service_env_rejects_conflicting_main_model_aliases(tmp_path) -> None:
    source = tmp_path / ".env"
    output = tmp_path / "gateway.env"
    source.write_text(
        "APP_ENV=local\n"
        "MAIN_MODEL_BASE_URL=http://canonical.example:9401/v1\n"
        "MAIN_LLM_BASE_URL=http://legacy.example:9401/v1\n"
        "MAIN_MODEL_STATIC_PROFILE=gemma4-12b-unified-fp8\n",
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(RuntimeError, match="conflicting env keys MAIN_LLM_BASE_URL and MAIN_MODEL_BASE_URL"):
        render(
            target="linux-nvidia-static",
            source_env=source,
            output=output,
        )
