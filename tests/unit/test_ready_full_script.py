"""ready-full의 Main chat gate probe가 실제 모델 응답 계약과 맞는지 검증한다."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ops/ready_full.sh"


def test_main_model_gate_probe_has_enough_output_budget() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert '\\"content\\":\\"Reply with OK.\\"' in script
    assert 'READY_FULL_MAIN_MODEL_MAX_TOKENS="${READY_FULL_MAIN_MODEL_MAX_TOKENS:-16}"' in script
    assert '\\"max_tokens\\":${READY_FULL_MAIN_MODEL_MAX_TOKENS}' in script
    assert '\\"max_tokens\\":1,' not in script


def test_main_model_gate_probe_fails_fast_on_deterministic_contract_error() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert '"$error_code" == "UPSTREAM_SCHEMA_ERROR"' in script
    assert "main-model gate probe failed permanently" in script
