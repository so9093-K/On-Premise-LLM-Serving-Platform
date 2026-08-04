"""Kanana risk 모델용 transformers head_dim 가드 패치 스크립트의 생애주기를
검증한다: 패치 적용/검증, Dockerfile에 인라인이 아니라 별도 스크립트로 COPY돼
있는지, 패치가 이미 적용된 상태에서 제거 가능 여부 판정."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATCH_PATH = ROOT / "ops/patches/transformers_llama_head_dim_guard.py"


def load_patch_module():
    spec = importlib.util.spec_from_file_location("transformers_llama_head_dim_guard", PATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_patch_script_applies_and_verifies_temp_llama_config(tmp_path: Path) -> None:
    module = load_patch_module()
    target = tmp_path / "configuration_llama.py"
    metadata = tmp_path / "patch.json"
    target.write_text(
        "class LlamaConfig:\n"
        "    def validate_architecture(self):\n"
        "        if self.hidden_size % self.num_attention_heads != 0:\n"
        "            raise ValueError('bad shape')\n",
        encoding="utf-8",
    )

    assert module.main(["--target", str(target), "--metadata", str(metadata), "--json"]) == 0
    patched = target.read_text(encoding="utf-8")
    assert 'getattr(self, "head_dim", None) is None' in patched
    saved = json.loads(metadata.read_text(encoding="utf-8"))
    assert saved["patch_id"] == "transformers_llama_head_dim_guard"
    assert saved["status"] == "applied"
    assert saved["original_file_sha256"] != saved["patched_file_sha256"]

    assert module.main(["--target", str(target), "--metadata", str(metadata), "--verify", "--json"]) == 0


def test_patch_lifecycle_is_not_inline_in_dockerfile() -> None:
    dockerfile = (ROOT / "ops/images/vllm-unified/Dockerfile").read_text(encoding="utf-8")
    assert "COPY ops/patches/transformers_llama_head_dim_guard.py" in dockerfile
    assert "ai_model_serving.patch.transformers_llama_head_dim_guard" in dockerfile
    assert "src.replace" not in dockerfile
    assert "TRANSFORMERS_MIN_VERSION" in dockerfile


def test_patch_script_reports_removal_check_statuses(tmp_path: Path) -> None:
    module = load_patch_module()
    target = tmp_path / "configuration_llama.py"
    target.write_text(
        "if self.hidden_size % self.num_attention_heads != 0:\n"
        "    raise ValueError('bad shape')\n",
        encoding="utf-8",
    )
    old_result = module.removal_check(target)
    assert old_result["status"] == "old_guard_present"
    assert old_result["removal_candidate"] is False

    target.write_text(
        'if getattr(self, "head_dim", None) is None and self.hidden_size % self.num_attention_heads != 0:\n'
        "    raise ValueError('bad shape')\n",
        encoding="utf-8",
    )
    patched_result = module.removal_check(target)
    assert patched_result["status"] == "patch_present"
    assert patched_result["requires_unpatched_canary"] is True
