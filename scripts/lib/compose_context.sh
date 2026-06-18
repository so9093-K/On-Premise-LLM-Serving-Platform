#!/usr/bin/env bash

# Resolve the shared Docker Compose execution context.
#
# Precedence:
#   process COMPOSE_PROJECT_NAME
#   -> ENV_FILE COMPOSE_PROJECT_NAME
#   -> compose
#
# Callers may append exposure or generated override files to
# COMPOSE_CONTEXT_FILE_ARGS before invoking Docker Compose.
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
