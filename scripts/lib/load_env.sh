#!/usr/bin/env bash
# Load local .env values for developer scripts without overriding values already exported by the caller.
load_local_env() {
  local env_file="${1:-.env}"
  [[ -f "$env_file" ]] || return 0
  declare -A seen_keys=()
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line key value
    line="${raw_line#${raw_line%%[![:space:]]*}}"
    line="${line%${line##*[![:space:]]}}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    if [[ "$line" == export[[:space:]]* ]]; then
      echo "[load-env] unsupported export syntax in $env_file: $line" >&2
      return 2
    fi
    if [[ "$line" != *=* ]]; then
      echo "[load-env] expected KEY=VALUE in $env_file: $line" >&2
      return 2
    fi
    key="${line%%=*}"
    value="${line#*=}"
    if [[ "$key" =~ [[:space:]] || "$value" =~ ^[[:space:]] || "$value" =~ [[:space:]]$ ]]; then
      echo "[load-env] spaces around KEY=VALUE are not supported in $env_file: $line" >&2
      return 2
    fi
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "[load-env] invalid env key in $env_file: $key" >&2
      return 2
    fi
    if [[ -n "${seen_keys[$key]+x}" ]]; then
      echo "[load-env] duplicate env key in $env_file: $key" >&2
      return 2
    fi
    seen_keys[$key]=1
    if [[ "$value" == *"#"* ]]; then
      echo "[load-env] inline comments are not supported in $env_file for $key" >&2
      return 2
    fi
    if [[ "$value" == \"* || "$value" == *\" || "$value" == \'* || "$value" == *\' ]]; then
      echo "[load-env] quoted values are not supported in $env_file for $key" >&2
      return 2
    fi
    if [[ -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$env_file"
}

env_file_value() {
  local env_file="${1:-.env}"
  local wanted_key="${2:?env key required}"
  [[ -f "$env_file" ]] || return 1
  declare -A seen_keys=()
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line key value
    line="${raw_line#${raw_line%%[![:space:]]*}}"
    line="${line%${line##*[![:space:]]}}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    if [[ "$line" == export[[:space:]]* ]]; then
      echo "[load-env] unsupported export syntax in $env_file: $line" >&2
      return 2
    fi
    if [[ "$line" != *=* ]]; then
      echo "[load-env] expected KEY=VALUE in $env_file: $line" >&2
      return 2
    fi
    key="${line%%=*}"
    value="${line#*=}"
    if [[ "$key" =~ [[:space:]] || "$value" =~ ^[[:space:]] || "$value" =~ [[:space:]]$ ]]; then
      echo "[load-env] spaces around KEY=VALUE are not supported in $env_file: $line" >&2
      return 2
    fi
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "[load-env] invalid env key in $env_file: $key" >&2
      return 2
    fi
    if [[ -n "${seen_keys[$key]+x}" ]]; then
      echo "[load-env] duplicate env key in $env_file: $key" >&2
      return 2
    fi
    seen_keys[$key]=1
    [[ "$key" == "$wanted_key" ]] || continue
    if [[ "$value" == *"#"* ]]; then
      echo "[load-env] inline comments are not supported in $env_file for $key" >&2
      return 2
    fi
    if [[ "$value" == \"* || "$value" == *\" || "$value" == \'* || "$value" == *\' ]]; then
      echo "[load-env] quoted values are not supported in $env_file for $key" >&2
      return 2
    fi
    printf '%s\n' "$value"
    return 0
  done < "$env_file"
  return 1
}

local_env_first_value() {
  local env_file="${1:-.env}"
  shift
  local key value
  for key in "$@"; do
    value="$(env_file_value "$env_file" "$key" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  for key in "$@"; do
    value="${!key:-}"
    if [[ -n "$value" ]]; then
      printf '%s\n' "${value%%,*}"
      return 0
    fi
  done
  return 1
}
