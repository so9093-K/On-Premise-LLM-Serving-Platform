#!/usr/bin/env bash
# Registry image ref의 immutable digest 형식 계약.
#
# build/deploy 정책이 서로 다른 정규식을 유지하지 않도록 순수 판정만 이 파일이 소유한다.
# 로컬 Docker image ID(sha256:...)는 개발용 persistent pin에서 별도 허용되지만, registry에서
# publish/pull하는 artifact 입력은 반드시 name@sha256:<64 lowercase hex> 형식이어야 한다.

is_registry_digest_image_ref() {
  local image_ref="${1:-}"
  [[ "${image_ref}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]
}

require_registry_digest_image_ref() {
  local key="$1" image_ref="${2:-}"
  if is_registry_digest_image_ref "${image_ref}"; then
    return 0
  fi
  echo "[image-ref] ERROR: ${key} must be an immutable registry digest (name@sha256:<64 lowercase hex>)." >&2
  return 2
}
