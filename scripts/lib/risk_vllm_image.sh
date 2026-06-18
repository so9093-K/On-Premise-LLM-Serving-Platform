#!/usr/bin/env bash
# Resolve and repair the dedicated Kanana risk vLLM image tag.
#
# The risk detector must not run directly on the generic/base vLLM image. Kanana
# Prompt 2.1B has an explicit head_dim shape that requires a pinned runtime
# stack. Older .env files, or stale exported shell variables, may still set
# RISK_VLLM_IMAGE to VLLM_IMAGE. Treat that state as a migration target and
# repair it automatically instead of asking operators to run sed by hand.

risk_vllm_default_image() {
  local version
  version="$(cat VERSION 2>/dev/null || echo 0.0.0)"
  printf 'ai-model-serving-risk-vllm-kanana:%s\n' "$version"
}

risk_vllm_env_file_value() {
  local env_file="${1:-.env}"
  local key="${2:?env key required}"
  [[ -f "$env_file" ]] || return 1
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*($|#)/ {
      k=$1
      gsub(/[[:space:]]/, "", k)
      if (k == key) {
        sub(/^[^=]*=/, "")
        print
        found=1
        exit
      }
    }
    END { if (!found) exit 1 }
  ' "$env_file"
}

risk_vllm_set_env_file_value() {
  local env_file="${1:-.env}"
  local key="${2:?env key required}"
  local value="${3:?env value required}"
  [[ -f "$env_file" ]] || return 0
  if grep -qE "^[[:space:]]*${key}=" "$env_file"; then
    python3 - "$env_file" "$key" "$value" <<'PY'
from __future__ import annotations
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
out: list[str] = []
updated = False
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        k = stripped.split("=", 1)[0].strip()
        if k == key:
            out.append(f"{key}={value}")
            updated = True
            continue
    out.append(line)
if not updated:
    out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

risk_vllm_resolve_images() {
  local env_file="${1:-.env}"
  local _lib_dir _root default_main
  _lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _root="$(cd "${_lib_dir}/../.." && pwd)"
  if ! default_main="$(python3 -c "
import yaml, sys
from pathlib import Path
cfg = yaml.safe_load((Path(sys.argv[1]) / 'configs/recommended_images.yaml').read_text())
print(cfg['images']['vllm']['default'])
" "$_root" 2>/dev/null)"; then
    default_main="vllm/vllm-openai:gemma4-unified-cu129"
  fi
  local default_risk
  default_risk="$(risk_vllm_default_image)"

  local file_risk file_base file_main
  file_risk="$(risk_vllm_env_file_value "$env_file" RISK_VLLM_IMAGE 2>/dev/null || true)"
  file_base="$(risk_vllm_env_file_value "$env_file" RISK_VLLM_BASE_IMAGE 2>/dev/null || true)"
  file_main="$(risk_vllm_env_file_value "$env_file" VLLM_IMAGE 2>/dev/null || true)"

  # Prefer exported custom overrides for ad-hoc builds, but never allow the
  # dangerous shared/base-image value to survive.
  VLLM_IMAGE_RESOLVED="${VLLM_IMAGE:-${file_main:-$default_main}}"
  RISK_VLLM_BASE_IMAGE_RESOLVED="${RISK_VLLM_BASE_IMAGE:-${file_base:-$VLLM_IMAGE_RESOLVED}}"
  RISK_VLLM_IMAGE_RESOLVED="${RISK_VLLM_IMAGE:-${file_risk:-$default_risk}}"

  if [[ -z "$RISK_VLLM_IMAGE_RESOLVED" ]]; then
    RISK_VLLM_IMAGE_RESOLVED="$default_risk"
  fi

  if [[ "$RISK_VLLM_IMAGE_RESOLVED" == "$RISK_VLLM_BASE_IMAGE_RESOLVED" || "$RISK_VLLM_IMAGE_RESOLVED" == "$VLLM_IMAGE_RESOLVED" ]]; then
    local old="$RISK_VLLM_IMAGE_RESOLVED"
    RISK_VLLM_IMAGE_RESOLVED="$default_risk"
    echo "[risk-vllm-image] migrated RISK_VLLM_IMAGE from shared/base image '${old}' to '${RISK_VLLM_IMAGE_RESOLVED}'" >&2
    if [[ -f "$env_file" ]]; then
      risk_vllm_set_env_file_value "$env_file" RISK_VLLM_IMAGE "$RISK_VLLM_IMAGE_RESOLVED"
      echo "[risk-vllm-image] updated ${env_file}: RISK_VLLM_IMAGE=${RISK_VLLM_IMAGE_RESOLVED}" >&2
    fi
  fi

  export VLLM_IMAGE_RESOLVED
  export RISK_VLLM_BASE_IMAGE_RESOLVED
  export RISK_VLLM_IMAGE_RESOLVED
}
