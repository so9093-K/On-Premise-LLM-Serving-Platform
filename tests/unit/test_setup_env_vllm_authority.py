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
