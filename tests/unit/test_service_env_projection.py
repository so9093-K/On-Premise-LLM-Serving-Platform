from __future__ import annotations

import pytest

from scripts.config.render_service_env import render


def test_static_service_env_projects_canonical_main_model_keys(tmp_path) -> None:
    source = tmp_path / ".env"
    output = tmp_path / "gateway.env"
    source.write_text(
        "APP_ENV=local\n"
        "MAIN_MODEL_BASE_URL=http://host.example:9401/v1\n"
        "MAIN_MODEL_STATIC_PROFILE=gemma4-12b-unified-fp8\n"
        "MAIN_MODEL_ALIAS=local-main\n",
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


def test_static_service_env_requires_canonical_source_keys(tmp_path) -> None:
    source = tmp_path / ".env"
    output = tmp_path / "gateway.env"
    source.write_text(
        "APP_ENV=local\n"
        "MAIN_LLM_BASE_URL=http://legacy.example:9401/v1\n"
        "MAIN_LLM_STATIC_PROFILE=gemma4-12b-unified-fp8\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="missing required"):
        render(
            target="linux-nvidia-static",
            source_env=source,
            output=output,
        )
