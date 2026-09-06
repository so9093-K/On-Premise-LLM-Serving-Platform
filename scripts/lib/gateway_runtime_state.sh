#!/usr/bin/env bash
# 배포와 compose-up이 Gateway의 desired state(runtime-state.json)를 다루는 방법을
# 한 곳에 모은다. 두 경로가 같은 규칙을 각자 구현하면 조용히 갈라진다.
#
# 이 파일의 writer는 Gateway 하나다. 배포는 어떤 런타임을 정지 상태로 둘지 env로
# 전달만 하고, 기록은 Gateway가 기동 시 한 번 수행한다.

# 상태 파일이 놓이는 호스트 경로. compose 파일의 gateway bind mount 원본과 같아야
# 한다(ops/compose/full-stack.private-network.yaml의 `../../.runtime/gateway`).
# 배포에서는 release 디렉터리의 `.runtime`이 배포 루트로 symlink되어 같은 곳을 가리킨다.
GATEWAY_RUNTIME_DIR_RELPATH=".runtime/gateway"

# 이 디렉터리는 컨테이너 안에서 non-root로 도는 Gateway가 유일하게 쓰는 곳이다.
# bind mount라 소유권은 호스트가 정하는데, 이미지 안의 uid와 호스트 uid 사이에는
# 아무 관계가 없다. 그래서 "누가 먼저 만들었나"가 소유권을 결정해 버린다:
#
#   - compose가 먼저 닿으면 Docker가 root 소유로 만든다 -> 컨테이너가 못 쓴다.
#   - 배포 사용자가 먼저 만들면 그 사용자 소유가 된다 -> 역시 컨테이너가 못 쓴다.
#
# 존재 여부만 확인하면 이미 잘못된 소유권으로 굳은 디렉터리를 그대로 통과시킨다.
# 그래서 매번 소유권까지 단언한다. uid는 하드코딩하지 않고 이미지에게 직접 묻는다
# -- 박아두면 이미지가 실행 사용자를 바꾸는 순간 다시 어긋난다.
ensure_gateway_runtime_dir() {
  local dir="$1"
  local image="$2"
  local parent name owner
  parent="$(dirname "${dir}")"
  name="$(basename "${dir}")"
  mkdir -p "${parent}" || return 1
  # docker의 bind mount는 절대 경로만 받는다. 상대 경로를 넘기면 named volume으로
  # 해석되어 엉뚱한 곳을 만든다.
  parent="$(cd "${parent}" && pwd)" || return 1

  if ! owner="$(docker run --rm --entrypoint sh "${image}" -c 'printf "%s:%s" "$(id -u)" "$(id -g)"')"; then
    echo "[runtime-state] ERROR: failed to read the runtime uid/gid from ${image}" >&2
    return 1
  fi

  # 호스트 사용자에게는 chown 권한이 없을 수 있으므로 컨테이너의 root로 단언한다.
  if ! docker run --rm -u 0:0 -v "${parent}:/mnt" --entrypoint sh "${image}" \
    -c "mkdir -p /mnt/${name} && chown ${owner} /mnt/${name}"; then
    echo "[runtime-state] ERROR: failed to prepare ${dir} for ${owner}" >&2
    return 1
  fi
  echo "[runtime-state] ${dir} owned by ${owner}"
}

# Gateway는 콤마로 구분된 runtime key 목록을 읽는다. 형식을 호출부마다 다시 만들면
# 한쪽만 바뀌었을 때 지시가 조용히 무시되므로 여기서만 만든다.
#
# release id는 재적용을 막는 토큰이다. 같은 id로 컨테이너가 재시작되면 Gateway는
# 지시를 다시 적용하지 않고 파일에 남은 운영자 상태를 따른다. 빈 값을 넘기면 이미
# 정해진 id(배포에서는 .env의 DEPLOY_RELEASE_ID)를 그대로 둔다.
export_deferred_runtime_directive() {
  local release_id="$1"
  shift
  local joined=""
  if (($#)); then
    printf -v joined '%s,' "$@"
    joined="${joined%,}"
  fi
  export DEPLOY_DEFERRED_RUNTIMES="${joined}"
  if [[ -n "${release_id}" ]]; then
    export DEPLOY_RELEASE_ID="${release_id}"
  fi
  if [[ -n "${joined}" ]]; then
    echo "[runtime-state] deferred runtime directive: ${joined}"
  fi
}

# 롤백 전용. 롤백은 배포 직전에 돌고 있던 런타임을 다시 띄우는데, 실패한 배포의
# Gateway가 이미 desired state를 stopped로 새겨 뒀을 수 있다. 그대로 두면
# 컨테이너는 떠 있는데 Gateway만 정지로 알고 라우팅을 거부한다 -- 실제로 발생했다.
# 복원된 .env의 이전 release id가 재적용을 열어주므로 여기서 되돌린다.
export_runtime_restore_directive() {
  local joined=""
  if (($#)); then
    printf -v joined '%s,' "$@"
    joined="${joined%,}"
    echo "[runtime-state] runtime restore directive: ${joined}"
  fi
  export DEPLOY_ACTIVE_RUNTIMES="${joined}"
}
