from __future__ import annotations

from pathlib import Path

from scripts.config import setup_env


def test_fresh_compose_env_uses_single_shared_vllm_authority(tmp_path: Path) -> None:
    out = tmp_path / ".env"

    rc = setup_env.main(["--profile", "compose", "--output", str(out)])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert values["VLLM_IMAGE"].startswith("ai-model-serving-vllm-unified:")
    assert "EMBEDDING_KO_VLLM_IMAGE" not in values
    assert "RISK_VLLM_IMAGE" not in values


def test_sync_env_removes_retired_embedding_ko_image_and_preserves_risk_compatibility(
    tmp_path: Path,
) -> None:
    out = tmp_path / ".env"
    out.write_text(
        "BUILD_PROFILE=compose\n"
        "VLLM_IMAGE=registry.example.com/vllm@sha256:shared\n"
        "EMBEDDING_KO_VLLM_IMAGE=registry.example.com/vllm@sha256:ko\n"
        "RISK_VLLM_IMAGE=registry.example.com/vllm@sha256:risk\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--sync-env", "--env-file", str(out)])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert values["VLLM_IMAGE"] == "registry.example.com/vllm@sha256:shared"
    assert "EMBEDDING_KO_VLLM_IMAGE" not in values
    assert values["RISK_VLLM_IMAGE"] == "registry.example.com/vllm@sha256:risk"


def test_force_removes_retired_embedding_ko_image_and_preserves_risk_compatibility(
    tmp_path: Path,
) -> None:
    out = tmp_path / ".env"
    out.write_text(
        "VLLM_IMAGE=registry.example.com/vllm@sha256:shared\n"
        "EMBEDDING_KO_VLLM_IMAGE=registry.example.com/vllm@sha256:ko\n"
        "RISK_VLLM_IMAGE=registry.example.com/vllm@sha256:risk\n",
        encoding="utf-8",
    )

    rc = setup_env.main(["--profile", "compose", "--output", str(out), "--force"])

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert "EMBEDDING_KO_VLLM_IMAGE" not in values
    assert values["RISK_VLLM_IMAGE"] == "registry.example.com/vllm@sha256:risk"


def test_explicit_risk_image_flag_remains_compatibility_input(tmp_path: Path) -> None:
    out = tmp_path / ".env"

    rc = setup_env.main(
        [
            "--profile",
            "compose",
            "--output",
            str(out),
            "--risk-vllm-image",
            "registry.example.com/vllm@sha256:risk",
        ]
    )

    assert rc == 0
    values = setup_env.read_env_values(out)
    assert values["RISK_VLLM_IMAGE"] == "registry.example.com/vllm@sha256:risk"
