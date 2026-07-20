#!/usr/bin/env bash

# 공유되는 Docker Compose 실행 컨텍스트를 확정합니다.
#
# 우선순위:
#   프로세스 COMPOSE_PROJECT_NAME
#   -> ENV_FILE COMPOSE_PROJECT_NAME
#   -> compose
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
        --env-file "$ENV_FILE_ABS" COMPOSE_PROJECT_NAME --default compose
    )"
  fi
  project_name="${project_name:-compose}"

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

compose_context_run() {
  docker compose \
    "${COMPOSE_CONTEXT_FILE_ARGS[@]}" \
    --env-file "$ENV_FILE_ABS" \
    "$@"
}
