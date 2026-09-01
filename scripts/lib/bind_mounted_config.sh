#!/usr/bin/env bash

# 배포가 자동으로 반영해도 되는 bind-mounted 설정 목록을 compose 파일에서 파생한다.
# "compose-service:source path ..." 형식으로 한 줄씩 출력한다.
#
# 예전에는 이 목록을 손으로 적어뒀다. compose와 갈라져도 확인하는 코드가 없어서
# promtail이 compose에서 사라진 뒤에도 배포 상태 파일이 남아 있었고, 적어둔 경로가
# 실제 마운트보다 거칠어서(예: 파일 두 개만 마운트하는데 `ops/prometheus` 전체)
# 지문 범위도 실제와 달랐다.
#
# 파생 규칙:
#   - release 상대 경로(`..`로 시작)를 bind-mount하는 서비스만 본다.
#   - `.runtime/*`는 뺀다. release 안의 symlink로 공유 상태를 가리키므로 release마다
#     내용이 달라지지 않는다. 배포 서버에서는 realpath로도 걸러지지만, 로컬 저장소
#     에서는 실제 디렉터리라 이름으로도 함께 판정해야 두 환경에서 결과가 같다.
#   - release 트리 밖으로 나가는 경로도 뺀다.
#   - GPU를 예약한 서비스는 뺀다. 설정 변경으로 모델을 콜드 스타트시키지 않는다는
#     정책이며, 모델 런타임 입력은 의도적인 교체 경로가 담당한다. 이 판단만이
#     compose에서 파생되지 않는 정책인데, GPU 예약 자체는 compose가 이미 선언하고
#     있으므로 여기에 서비스 이름을 적을 필요는 없다.
bind_mounted_config_service_specs() {
  local release_root="${1:?release root required}"
  local compose_file="${2:?compose file required}"
  "${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}" - \
    "${release_root}" "${compose_file}" <<'PY'
import os
import sys

import yaml

release_root, compose_file = sys.argv[1], sys.argv[2]
root = os.path.realpath(release_root)
compose_path = os.path.join(root, compose_file)
compose_dir = os.path.dirname(compose_path)
with open(compose_path, encoding="utf-8") as handle:
    document = yaml.safe_load(handle)

for service, definition in sorted((document.get("services") or {}).items()):
    reservations = (
        ((definition.get("deploy") or {}).get("resources") or {}).get("reservations") or {}
    )
    if reservations.get("devices"):
        continue
    paths = []
    for volume in definition.get("volumes") or []:
        if not isinstance(volume, str) or not volume.startswith(".."):
            continue
        joined = os.path.normpath(os.path.join(compose_dir, volume.split(":", 1)[0]))
        if ".runtime" in os.path.relpath(joined, root).split(os.sep):
            continue
        source = os.path.realpath(joined)
        if source == root or os.path.commonpath([source, root]) != root:
            continue
        paths.append(os.path.relpath(source, root))
    if paths:
        print("{}:{}".format(service, " ".join(sorted(set(paths)))))
PY
}

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
