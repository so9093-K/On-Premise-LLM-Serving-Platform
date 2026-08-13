#!/usr/bin/env python3
"""Carry two local fixes for Gemma 4 *unified* multimodal on the pinned
``vllm/vllm-openai:v0.25.1-cu129`` + transformers stack.

원래는 vLLM ``0.1.dev17235+gf52870f26.d20260603`` / transformers ``5.10.1``
기준으로 작성됐다. vLLM ``0.25.1``(transformers ``5.13.1``)로 올리면서 같은
생성자 호출에 ``prefix=`` 인자가 추가돼 FIX 1의 anchor를 갱신했다 — 실제 GPU
로드(12B FP8 프로필, 구조화 출력 요청)로 이 버전에서 ``Using V2 Model Runner``가
선택되고, 이 프로젝트의 웜업 우회 코드가 존재하는 이유였던
``apply_token_bitmask_inplace_kernel`` JIT 갭이 더 이상 재현되지 않는 것까지
확인했다. 그래도 여전히 upstream 버그라 정식으로 고쳐질 때까지 이미지가 로컬
fix를 들고 있는 것이다. 각 수정은 upstream 레이아웃에 대한 ``assert``로
보호되어 있어서, base 이미지가 바뀌어 패치가 안 맞으면 조용히 깨진 런타임을
띄우는 대신 빌드가 시끄럽게 실패한다.

Without these, ``gemma4-12b-unified-fp8`` serves text correctly but:
  * image requests return pad-only output (``finish_reason=length``); and
  * the multimodal warmup crashes at boot (which also blocks audio).
Text-only profiles (incl. 26B) are unaffected either way.

FIX 1 (image) — vLLM FP8 ignore-list prefix mismatch
  The checkpoint's quantization ``ignore`` list names the vision projection
  ``model.vision_embedder.patch_dense``, but vLLM matches against its bare internal
  module name ``vision_embedder.patch_dense``. The mismatch means ``patch_dense`` is
  wrongly FP8-quantized, corrupting the BF16 vision projection -> pad-only output.
  ``patch_dense`` is never quantized for this checkpoint, so build it unquantized.

FIX 2 (audio warmup) — vLLM/transformers API mismatch
  vLLM ``gemma4_mm.get_dummy_mm_data`` reads ``feature_extractor.fft_length``, which
  transformers' ``Gemma4UnifiedAudioFeatureExtractor`` does not define -> the
  all-modality warmup crashes. The value only sizes dummy warmup audio; real
  inference never reads it. Define it (validated value: 400).
"""
import os
import re

import transformers
import vllm

# --- FIX 1: vision_embedder.patch_dense must be unquantized -------------------
gemma4_unified = os.path.join(
    os.path.dirname(vllm.__file__),
    "model_executor/models/gemma4_unified.py",
)
src = open(gemma4_unified).read()
needle = (
    "            bias=True,\n"
    "            quant_config=quant_config,\n"
    "            prefix=f\"{prefix}.patch_dense\",\n"
    "            gather_output=True,"
)
assert needle in src, "patch_dense block not found — vLLM gemma4_unified.py layout changed"
patched = src.replace(
    needle,
    (
        "            bias=True,\n"
        "            quant_config=None,  # LOCAL FIX: ignore-list prefix mismatch else FP8-corrupts vision proj\n"
        "            prefix=f\"{prefix}.patch_dense\",\n"
        "            gather_output=True,"
    ),
    1,
)
open(gemma4_unified, "w").write(patched)

# --- FIX 2: Gemma4UnifiedAudioFeatureExtractor.fft_length ---------------------
audio_fe = os.path.join(
    os.path.dirname(transformers.__file__),
    "models/gemma4_unified/feature_extraction_gemma4_unified.py",
)
fe_src = open(audio_fe).read()
assert "class Gemma4UnifiedAudioFeatureExtractor" in fe_src, (
    "audio FE class not found — transformers layout changed"
)
if not re.search(r"^\s+fft_length\s*=", fe_src, re.M):
    fe_patched = re.sub(
        r"(class Gemma4UnifiedAudioFeatureExtractor\([^)]*\):[ \t]*\n)",
        r"\1    fft_length = 400  # LOCAL FIX: vLLM dummy-audio length; unused by real inference\n",
        fe_src,
        count=1,
    )
    assert fe_patched != fe_src, "failed to inject fft_length"
    open(audio_fe, "w").write(fe_patched)

# --- Verify both edits are present on disk before the build proceeds ----------
assert "quant_config=None,  # LOCAL FIX" in open(gemma4_unified).read()
assert re.search(r"^\s+fft_length\s*=\s*400", open(audio_fe).read(), re.M)
print("gemma4 unified multimodal patches applied: patch_dense unquantized + fft_length=400")
