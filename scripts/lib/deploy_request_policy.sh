#!/usr/bin/env bash
# CI가 원격 서버에 접속하기 전에 결정할 수 있는 배포 요청 정책이다.
#
# 이 파일은 네트워크·파일 시스템·Docker 상태를 읽지 않는다. 원격 배포 절차와
# 분리해 두면, 어떤 입력이 rolling/full 배포를 요청하는지 로컬과 CI에서 같은
# 방식으로 검증할 수 있다. 호출자는 필요한 변수를 설정한 뒤 아래 함수를 순서대로
# 호출하고, 실패 시 반환 코드를 그대로 배포 실패로 처리한다.

_DEPLOY_REQUEST_POLICY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_DEPLOY_REQUEST_POLICY_LIB_DIR}/image_ref_policy.sh"
unset _DEPLOY_REQUEST_POLICY_LIB_DIR

deploy_normalize_runtime_image_inputs() {
  if [[ -n "${MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY:-}" &&
    -n "${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}" &&
    "${MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY}" != "${AUDIO_VLLM_IMAGE_TO_DEPLOY}" ]]; then
    echo "[deploy] ERROR: MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY conflicts with legacy AUDIO_VLLM_IMAGE_TO_DEPLOY." >&2
    return 2
  fi
  if [[ -z "${MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY:-}" &&
    -n "${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
    MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY="${AUDIO_VLLM_IMAGE_TO_DEPLOY}"
  fi
}

deploy_resolve_mode() {
  deploy_normalize_runtime_image_inputs || return 2
  # 새로 빌드·publish한 unified image는 그 digest를 모든 vLLM runtime에 같이
  # 적용해야 한다. 사용자가 rolling을 요청했더라도 image를 빌드한 사실이 더
  # 구체적인 의도이므로 full로 승격한다. 일반 full/rolling은 기존 pin을 유지한다.
  if [[ -n "${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}" ]]; then
    DEPLOY_MODE="full"
    DEPLOY_MODE_REASON="fresh unified vLLM image artifact"
    return 0
  fi

  # Registry를 정리하는 운영 환경에서는 필요한 unified image가 없을 수 있으므로
  # full이 기본이다. 빠른 platform-only 배포만 DEPLOY_MODE=rolling으로 명시한다.
  if [[ -n "${DEPLOY_MODE:-}" ]]; then
    return 0
  fi

  DEPLOY_MODE="full"
  DEPLOY_MODE_REASON="default full deployment policy"
}

deploy_validate_request() {
  local release_id="$1" releases_to_keep="$2" key value
  deploy_normalize_runtime_image_inputs || return 2

  if [[ ! "${release_id}" =~ ^[A-Za-z0-9._-]{1,128}$ ]]; then
    echo "[deploy] ERROR: DEPLOY_RELEASE_ID must contain only A-Za-z0-9._- and be <=128 chars." >&2
    return 2
  fi
  if [[ ! "${releases_to_keep}" =~ ^[1-9][0-9]*$ ]]; then
    echo "[deploy] ERROR: RELEASES_TO_KEEP must be a positive integer." >&2
    return 2
  fi

  if ! require_registry_digest_image_ref \
    "PLATFORM_IMAGE_TO_DEPLOY" "${PLATFORM_IMAGE_TO_DEPLOY:-}"; then
    return 2
  fi
  if [[ -n "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" ]]; then
    echo "[deploy] ERROR: RISK_VLLM_IMAGE_TO_DEPLOY is retired." >&2
    echo "[deploy] Use VLLM_UNIFIED_IMAGE_TO_DEPLOY for shared vLLM image promotion." >&2
    return 2
  fi
  for key in \
    VLLM_UNIFIED_IMAGE_TO_DEPLOY \
    MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY
  do
    value="${!key:-}"
    if [[ -n "${value}" ]] && ! require_registry_digest_image_ref "${key}" "${value}"; then
      return 2
    fi
  done

  case "${DEPLOY_MODE:-}" in
    rolling|full) ;;
    *)
      echo "[deploy] ERROR: DEPLOY_MODE must be rolling or full, got: ${DEPLOY_MODE:-}" >&2
      return 2
      ;;
  esac

  if [[ "${DEPLOY_MODE}" != "full" &&
    ( -n "${DEPLOY_RUNTIME_PROFILE:-}" || -n "${DEPLOY_DEFERRED_RUNTIMES:-}" ) ]]; then
    echo "[deploy] ERROR: DEPLOY_RUNTIME_PROFILE/DEPLOY_DEFERRED_RUNTIMES require DEPLOY_MODE=full." >&2
    echo "[deploy] Runtime startup policy mutates Gateway desired runtime state and must not run in rolling deploys." >&2
    return 2
  fi

  if [[ "${DEPLOY_MODE}" != "full" &&
    ( -n "${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}" || -n "${MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY:-}" ) ]]; then
    echo "[deploy] ERROR: runtime image promotion inputs require DEPLOY_MODE=full." >&2
    return 2
  fi
}
