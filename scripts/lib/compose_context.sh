#!/usr/bin/env bash

# 공유되는 Docker Compose 실행 컨텍스트를 확정합니다.
#
# 우선순위:
#   프로세스 COMPOSE_PROJECT_NAME
#   -> ENV_FILE COMPOSE_PROJECT_NAME
#   -> ai-model-serving-platform
#
# 호출하는 쪽에서는 Docker Compose를 실행하기 전에 exposure나 생성된 override
# 파일을 COMPOSE_CONTEXT_FILE_ARGS에 추가할 수 있습니다.
compose_context_init() {
  local root="${1:?project root required}"
  local python_bin="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
  local project_name="${COMPOSE_PROJECT_NAME:-}"

  ENV_FILE="${ENV_FILE:-.env}"
  COMPOSE_FILE="${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"
  ENV_FILE_ABS="$("$python_bin" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$ENV_FILE")"
  COMPOSE_FILE_ABS="$("$python_bin" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$COMPOSE_FILE")"

  if [[ -z "$project_name" && -f "$ENV_FILE_ABS" ]]; then
    project_name="$(
      "$python_bin" "$root/scripts/env/env_get.py" \
        --env-file "$ENV_FILE_ABS" COMPOSE_PROJECT_NAME --default ai-model-serving-platform
    )"
  fi
  project_name="${project_name:-ai-model-serving-platform}"

  COMPOSE_PROJECT_NAME="$project_name"
  COMPOSE_PROJECT_NAME_EFFECTIVE="$project_name"
  COMPOSE_SERVICE_ENV_FILE="$ENV_FILE_ABS"
  COMPOSE_CONTEXT_FILE_ARGS=(
    --project-name "$COMPOSE_PROJECT_NAME_EFFECTIVE"
    -f "$COMPOSE_FILE_ABS"
  )

  export \
    ENV_FILE COMPOSE_FILE ENV_FILE_ABS COMPOSE_FILE_ABS \
    COMPOSE_PROJECT_NAME COMPOSE_PROJECT_NAME_EFFECTIVE COMPOSE_SERVICE_ENV_FILE
}

# 동일 Docker daemon에서 다른 working directory가 같은 Compose project name을
# 사용 중이면 up/down/restart는 그 컨테이너를 재생성·중지할 수 있다. 개발 명령은
# 이를 기본 거부한다. release 경로를 의도적으로 넘나드는 배포 자동화만
# ALLOW_SHARED_COMPOSE_PROJECT=1을 명시할 수 있다.
compose_context_assert_mutation_safe() {
  local expected_dir actual_dir container_id
  [[ "${ALLOW_SHARED_COMPOSE_PROJECT:-0}" == "1" ]] && return 0
  command -v docker >/dev/null 2>&1 || return 0

  expected_dir="$(readlink -f "$(dirname "$COMPOSE_FILE_ABS")")"
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    actual_dir="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container_id" 2>/dev/null || true)"
    [[ -n "$actual_dir" ]] || continue
    actual_dir="$(readlink -f "$actual_dir" 2>/dev/null || printf '%s' "$actual_dir")"
    if [[ "$actual_dir" != "$expected_dir" ]]; then
      echo "[compose] refusing to mutate project '$COMPOSE_PROJECT_NAME_EFFECTIVE': it has a container from $actual_dir" >&2
      echo "[compose] current command uses $expected_dir. Choose a unique COMPOSE_PROJECT_NAME for this environment." >&2
      echo "[compose] Only intentional release orchestration may set ALLOW_SHARED_COMPOSE_PROJECT=1." >&2
      return 2
    fi
  done < <(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME_EFFECTIVE" 2>/dev/null || true)
}

compose_context_run() {
  docker compose \
    "${COMPOSE_CONTEXT_FILE_ARGS[@]}" \
    --env-file "$ENV_FILE_ABS" \
    "$@"
}
