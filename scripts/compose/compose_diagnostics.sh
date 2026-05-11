#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.example.yaml}"
ENV_FILE="${ENV_FILE:-.env}"
TAIL_LINES="${COMPOSE_DIAGNOSTIC_TAIL_LINES:-120}"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "[diagnostics] docker compose is unavailable; cannot collect compose diagnostics" >&2
  exit 0
fi

echo "[diagnostics] docker compose ps"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps || true

services=(gateway risk-adapter main-llm-vllm embedding-vllm risk-prompt-vllm risk-siren-vllm prometheus grafana dcgm-exporter cadvisor)
for service in "${services[@]}"; do
  echo
  echo "[diagnostics] logs --tail=${TAIL_LINES} ${service}"
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" logs --tail="$TAIL_LINES" "$service" 2>&1 | tee "/tmp/${service}.compose.log" || true

  if grep -q "max_num_batched_tokens .* is smaller than max_model_len" "/tmp/${service}.compose.log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected invalid vLLM batching config; set max_num_batched_tokens >= max_model_len for this runtime."
  fi
  if grep -q "hidden size .* is not a multiple of the number of attention heads" "/tmp/${service}.compose.log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected LlamaConfig validate_architecture failure. 원인: transformers 4.52.0–4.52.3 버그 (explicit head_dim 모델 거부) + huggingface_hub >= 1.13.0의 init_with_validate 강화. 조치: RISK_VLLM_IMAGE를 transformers>=4.52.4로 재빌드하세요: make rebuild-risk-vllm"
  fi
  if grep -q "No available memory for the cache blocks" "/tmp/${service}.compose.log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected KV-cache memory allocation failure; tune gpu_memory_utilization/context/batching or isolate this runtime."
  fi
  if grep -q "Engine core initialization failed" "/tmp/${service}.compose.log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected vLLM engine core crash (Engine core initialization failed). GPU OOM 가능성 높음. 확인 항목: risk 모델에 --enforce-eager 설정 여부, 총 gpu_memory_utilization < 0.90, compose의 depends_on healthcheck 체인으로 순차 기동 여부."
  fi
  if grep -q "executable file not found" "/tmp/${service}.compose.log" 2>/dev/null; then
    echo "[diagnostics] ${service}: detected container entrypoint executable error."
  fi
  rm -f "/tmp/${service}.compose.log"
done
