#!/usr/bin/env bash
# Remote deployment .env helpers. COMPOSE_ENV_FILE is owned by the caller.

deploy_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$COMPOSE_ENV_FILE"
}

deploy_export_compose_env() {
  local key value
  if ((${#COMPOSE_EXPORTED_KEYS[@]})); then unset "${COMPOSE_EXPORTED_KEYS[@]}"; fi
  COMPOSE_EXPORTED_KEYS=()
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
    COMPOSE_EXPORTED_KEYS+=("$key")
  done < "$COMPOSE_ENV_FILE"
}

deploy_set_env_value() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$COMPOSE_ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$COMPOSE_ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$COMPOSE_ENV_FILE"
  fi
}
