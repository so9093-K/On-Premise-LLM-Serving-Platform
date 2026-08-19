#!/usr/bin/env bash

# 자동 반영해도 GPU 모델 재기동 없이 영향 범위를 제한할 수 있는 monitoring
# bind-mounted 설정의 단일 목록이다. 각 항목은
# "compose-service:source path ..." 형식이다. 모델 런타임 입력은 이 목록에
# 넣지 않고, 의도적인 모델 교체 경로에서만 반영한다.
BIND_MOUNTED_CONFIG_SERVICE_SPECS=(
  "grafana:ops/grafana/dashboards ops/grafana/provisioning"
  "prometheus:ops/prometheus"
  "loki:ops/loki"
  "alloy:ops/alloy"
)

# 주어진 source root 아래의 설정 내용으로 안정적인 fingerprint를 만든다.
# 파일명도 sha256sum 입력에 포함되므로, 내용 변경뿐 아니라 추가/삭제도 감지한다.
bind_mounted_config_fingerprint() (
  local source_root="$1"
  shift
  cd "${source_root}"
  {
    local relative_path
    for relative_path in "$@"; do
      find -- "${relative_path}" -type f -print0
    done
  } | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
)

# 컨테이너가 실제로 어느 release 디렉터리에 bind mount됐는지 돌려준다.
#
# Docker는 mount source를 컨테이너 시작 시점에 실제 경로로 고정하지만, 조회 API
# (`.Mounts[].Source`, compose working_dir 라벨)는 우리가 넘긴 `current` 심볼릭
# 경로를 그대로 돌려준다. 그래서 그 값들로는 어느 release에 묶였는지 알 수 없다 --
# 모든 컨테이너가 release와 무관하게 같은 문자열을 보고한다. 커널이 마운트를 해석한
# 결과인 /proc/<pid>/mountinfo만 실제 경로를 담고 있고, 소스가 지워진 마운트에는
# `//deleted`가 붙는다.
container_bound_release() {
  local container_id="$1" deploy_path="$2" pid
  pid="$(docker inspect -f '{{.State.Pid}}' "${container_id}" 2>/dev/null || true)"
  [[ -n "${pid}" && "${pid}" != "0" && -r "/proc/${pid}/mountinfo" ]] || return 1
  grep -oE "${deploy_path}/releases/[0-9a-f]{40}" "/proc/${pid}/mountinfo" 2>/dev/null |
    LC_ALL=C sort -u | head -n 1
}

# 실행 중인 컨테이너가 아직 bind mount 중인 release 디렉터리 목록.
# prune이 이 목록을 지우면 살아있는 컨테이너의 마운트가 통째로 죽는다.
releases_in_use_by_running_containers() {
  local deploy_path="$1" container_id pid
  for container_id in $(docker ps -q 2>/dev/null); do
    pid="$(docker inspect -f '{{.State.Pid}}' "${container_id}" 2>/dev/null || true)"
    [[ -n "${pid}" && "${pid}" != "0" && -r "/proc/${pid}/mountinfo" ]] || continue
    grep -oE "${deploy_path}/releases/[0-9a-f]{40}" "/proc/${pid}/mountinfo" 2>/dev/null || true
  done | LC_ALL=C sort -u
}
