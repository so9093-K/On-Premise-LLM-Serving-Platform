#!/usr/bin/env bash

# 자동 반영해도 GPU 모델 재기동 없이 영향 범위를 제한할 수 있는 monitoring
# bind-mounted 설정의 단일 목록이다. 각 항목은
# "compose-service:source path ..." 형식이다. 모델 런타임 입력은 이 목록에
# 넣지 않고, 의도적인 모델 교체 경로에서만 반영한다.
BIND_MOUNTED_CONFIG_SERVICE_SPECS=(
  "grafana:ops/grafana/dashboards ops/grafana/provisioning"
  "prometheus:ops/prometheus"
  "loki:ops/loki"
  "promtail:ops/promtail"
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
