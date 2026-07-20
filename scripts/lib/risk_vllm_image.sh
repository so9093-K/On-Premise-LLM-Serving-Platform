#!/usr/bin/env bash
# 전용 Kanana risk vLLM 이미지 태그를 확정하고 복구합니다.
#
# risk detector는 범용/base vLLM 이미지에서 직접 실행되면 안 됩니다. Kanana
# Prompt 2.1B는 명시적인 head_dim shape를 가지고 있어 고정된 런타임 스택이
# 필요합니다. 오래된 .env 파일이나 남아 있는 export된 shell 변수가 여전히
# RISK_VLLM_IMAGE를 VLLM_IMAGE로 설정하고 있을 수 있습니다. 이 상태를 마이그레이션
# 대상으로 간주하고, 운영자가 수동으로 sed를 돌리게 하는 대신 자동으로 복구합니다.

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

  # 임시 빌드를 위해 export된 custom override 값을 우선 사용하되, 위험한
  # shared/base-image 값이 그대로 남아있는 것은 절대 허용하지 않습니다.
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
