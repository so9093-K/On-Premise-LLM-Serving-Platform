#!/usr/bin/env bash
# CI가 원격 서버에 접속하기 전에 결정할 수 있는 배포 요청 정책이다.
#
# 이 파일은 네트워크·파일 시스템·Docker 상태를 읽지 않는다. 원격 배포 절차와
# 분리해 두면, 어떤 입력이 rolling/full 배포를 요청하는지 로컬과 CI에서 같은
# 방식으로 검증할 수 있다. 호출자는 필요한 변수를 설정한 뒤 아래 함수를 순서대로
# 호출하고, 실패 시 반환 코드를 그대로 배포 실패로 처리한다.

deploy_resolve_mode() {
  # Registry를 정리하는 운영 환경에서는 필요한 unified image가 없을 수 있으므로
  # full이 기본이다. 빠른 platform-only 배포만 DEPLOY_MODE=rolling으로 명시한다.
  if [[ -n "${DEPLOY_MODE:-}" ]]; then
    return 0
  fi

  DEPLOY_MODE="full"
  DEPLOY_MODE_REASON="default full deployment policy"
}

deploy_validate_request() {
  local release_id="$1" releases_to_keep="$2"

  if [[ ! "${release_id}" =~ ^[A-Za-z0-9._-]{1,128}$ ]]; then
    echo "[deploy] ERROR: DEPLOY_RELEASE_ID must contain only A-Za-z0-9._- and be <=128 chars." >&2
    return 2
  fi
  if [[ ! "${releases_to_keep}" =~ ^[1-9][0-9]*$ ]]; then
    echo "[deploy] ERROR: RELEASES_TO_KEEP must be a positive integer." >&2
    return 2
  fi

  case "${DEPLOY_MODE:-}" in
    rolling|full) ;;
    *)
      echo "[deploy] ERROR: DEPLOY_MODE must be rolling or full, got: ${DEPLOY_MODE:-}" >&2
      return 2
      ;;
  esac

  if [[ "${DEPLOY_MODE}" == "full" && "${RUN_READY_FULL_SMOKE:-}" != "1" ]]; then
    echo "[deploy] ERROR: full deploy requires RUN_READY_FULL_SMOKE=1 so make ready-full cannot be skipped." >&2
    return 2
  fi
  if [[ "${DEPLOY_MODE}" != "full" &&
    ( -n "${DEPLOY_RUNTIME_PROFILE:-}" || -n "${DEPLOY_DEFERRED_RUNTIMES:-}" ) ]]; then
    echo "[deploy] ERROR: DEPLOY_RUNTIME_PROFILE/DEPLOY_DEFERRED_RUNTIMES require DEPLOY_MODE=full." >&2
    echo "[deploy] Runtime startup policy mutates Gateway desired runtime state and must not run in rolling deploys." >&2
    return 2
  fi
}

deploy_resolve_full_runtime_images() {
  [[ "${DEPLOY_MODE:-}" == "full" ]] || return 0

  RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}}"
  if [[ -z "${RISK_VLLM_IMAGE_TO_DEPLOY}" ]]; then
    echo "[deploy] ERROR: full deploy requires RISK_VLLM_IMAGE_TO_DEPLOY or a fresh VLLM_UNIFIED_IMAGE_TO_DEPLOY digest." >&2
    return 2
  fi

  # unified 이미지는 risk-prompt와 12B profile이 함께 소비한다. 호출자가 12B 전용
  # override를 명시하지 않았다면 같은 immutable ref를 써야 한쪽만 바뀌는 drift가 없다.
  AUDIO_VLLM_IMAGE_TO_DEPLOY="${AUDIO_VLLM_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_TO_DEPLOY}}"
}
