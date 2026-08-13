#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
ENV_FILE="${ENV_FILE:-.env}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
source scripts/lib/compose_context.sh
compose_context_init "$ROOT"
TAIL_LINES="${COMPOSE_DIAGNOSTIC_TAIL_LINES:-120}"
DIAGNOSTIC_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ai-model-serving-diagnostics.XXXXXX")"
trap 'rm -rf "$DIAGNOSTIC_TMP"' EXIT
GPU_AVOID_ABOVE="$("$PYTHON_BIN" - <<'PY' 2>/dev/null || echo "configs/gpu_budgets.yaml avoid_above"
from pathlib import Path
import yaml

doc = yaml.safe_load(Path("configs/gpu_budgets.yaml").read_text(encoding="utf-8"))
print(doc["gpu"]["total_gpu_memory_utilization"]["avoid_above"])
PY
)"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "[diagnostics] docker compose is unavailable; cannot collect compose diagnostics" >&2
  exit 0
fi

echo "[diagnostics] docker compose ps"
compose_context_run ps || true

services=(gateway risk-adapter main-llm-vllm embedding-vllm embedding-ko-vllm risk-prompt-vllm prometheus grafana dcgm-exporter cadvisor loki alloy)
for service in "${services[@]}"; do
  echo
  echo "[diagnostics] logs --tail=${TAIL_LINES} ${service}"
  service_log="${DIAGNOSTIC_TMP}/${service}.compose.log"
  compose_context_run logs --tail="$TAIL_LINES" "$service" 2>&1 | tee "$service_log" || true

  if grep -q "max_num_batched_tokens .* is smaller than max_model_len" "$service_log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected invalid vLLM batching config; set max_num_batched_tokens >= max_model_len for this runtime."
  fi
  if grep -q "hidden size .* is not a multiple of the number of attention heads" "$service_log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected LlamaConfig validate_architecture failure. 원인: transformers 4.52.0–4.52.3 버그 (explicit head_dim 모델 거부) + huggingface_hub >= 1.13.0의 init_with_validate 강화. 조치: RISK_VLLM_IMAGE를 transformers>=4.52.4로 재빌드하세요: make build-vllm-unified-image"
  fi
  if grep -q "No available memory for the cache blocks" "$service_log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected KV-cache memory allocation failure; tune gpu_memory_utilization/context/batching or isolate this runtime."
  fi
  if grep -q "kv-cache is not supported with fp8 checkpoints" "$service_log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected unsupported kv_cache_dtype for an FP8 checkpoint. Remove --kv-cache-dtype from the active runtime policy for this model/image combination."
  fi
  if grep -q "Engine core initialization failed" "$service_log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected vLLM engine core crash (Engine core initialization failed). GPU OOM 가능성 높음. 확인 항목: risk 모델에 --enforce-eager 설정 여부, 총 gpu_memory_utilization < ${GPU_AVOID_ABOVE}, compose의 depends_on healthcheck 체인으로 순차 기동 여부."
  fi
  if grep -q "executable file not found" "$service_log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected container entrypoint executable error."
  fi
done
