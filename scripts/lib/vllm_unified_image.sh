#!/usr/bin/env bash
# vLLM unified 이미지 태그를 확정합니다.
#
# VLLM_IMAGE가 26B/12B/embedding/embedding-ko/risk-prompt가 공유하는 persistent
# runtime image authority다. 독립 runtime artifact lifecycle이 다시 필요해질 때는
# 별도 build, qualification, promotion, rollback 계약과 함께 새 authority를 정의한다.

vllm_unified_default_image() {
  local version
  version="$(cat VERSION 2>/dev/null || echo 0.0.0)"
  printf 'ai-model-serving-vllm-unified:%s\n' "$version"
}

vllm_unified_canonical_base_image() {
  local root python_bin
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  python_bin="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
  "$python_bin" "$root/scripts/models/print_vllm_unified_compatibility.py" --key base_image
}

vllm_unified_env_file_value() {
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

vllm_unified_resolve_images() {
  local env_file="${1:-.env}"
  local default_image
  default_image="$(vllm_unified_default_image)"
  local canonical_base_image
  canonical_base_image="$(vllm_unified_canonical_base_image)"

  local file_main
  file_main="$(vllm_unified_env_file_value "$env_file" VLLM_IMAGE 2>/dev/null || true)"

  VLLM_IMAGE_RESOLVED="${VLLM_IMAGE:-${file_main:-$default_image}}"
  # base override는 프로세스 환경변수로만 받는다. .env는 일부러 읽지 않는다 --
  # base가 영속 파일에 적히면 값이 낡아도 아무도 모르고, 그 파일 하나 때문에
  # canonical digest가 조용히 무시된다(실제로 배포 서버 .env에 부팅 실패로 폐기된
  # base 태그가 남아 있었다). 그래서 VLLM_BASE_IMAGE는 env_contract.yaml의
  # removed_keys에 등록되어 sync-env가 .env에서 제거하며, 여기서도 읽지 않는다.
  # 한 번의 빌드에만 적용되는 override는 `VLLM_BASE_IMAGE=... make ...`로 준다.
  VLLM_BASE_IMAGE_RESOLVED="${VLLM_BASE_IMAGE:-$canonical_base_image}"
  # override는 반드시 immutable digest여야 한다. 태그를 허용하면 재현 불가능한
  # 이미지가 조용히 만들어진다(ops/images/vllm-unified/README.md).
  if [[ "$VLLM_BASE_IMAGE_RESOLVED" != *"@sha256:"* ]]; then
    echo "[vllm-unified] ERROR: VLLM_BASE_IMAGE must be a digest (name@sha256:...), got ${VLLM_BASE_IMAGE_RESOLVED}" >&2
    return 2
  fi

  export VLLM_IMAGE_RESOLVED
  export VLLM_BASE_IMAGE_RESOLVED
}
