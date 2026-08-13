#!/usr/bin/env bash
# 호출자가 이미 export한 값은 덮어쓰지 않으면서, 개발자 스크립트를 위해 로컬 .env 값을 로드합니다.
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

service_default_host_port() {
  local service_key="${1:?service key required}"
  "${PYTHON_BIN:?PYTHON_BIN must be set before reading service defaults}" - "$service_key" <<'PY'
from pathlib import Path
import sys

import yaml

service_key = sys.argv[1]
services = yaml.safe_load(Path("configs/services.yaml").read_text(encoding="utf-8"))["services"]
try:
    print(int(services[service_key]["default_host_port"]))
except KeyError as exc:
    raise SystemExit(f"services.yaml missing default_host_port for {service_key}") from exc
PY
}

model_serving_runtime_model_name() {
  local runtime_key="${1:?runtime key required}"
  "${PYTHON_BIN:?PYTHON_BIN must be set before reading model defaults}" - "$runtime_key" <<'PY'
from pathlib import Path
import sys

import yaml

runtime_key = sys.argv[1]
models = yaml.safe_load(Path("configs/model_serving.yaml").read_text(encoding="utf-8"))["models"]
try:
    print(str(models[runtime_key]["served_model_name"]))
except KeyError as exc:
    raise SystemExit(f"model_serving.yaml missing served_model_name for {runtime_key}") from exc
PY
}
