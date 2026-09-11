#!/usr/bin/env bash
set -euo pipefail

# Cheap generated artifacts only. Project reset owns environments, images and
# model caches; keeping those scopes out of clean makes this command repeatable.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRY_RUN=0
INCLUDE_LOGS=0
for option in "$@"; do
  case "$option" in
    --dry-run) DRY_RUN=1 ;;
    --logs) INCLUDE_LOGS=1 ;;
    *)
      echo "usage: $0 [--dry-run] [--logs]" >&2
      exit 2
      ;;
  esac
done

running=()
for name in gateway risk_adapter metal; do
  pid_file="$ROOT/run/${name}.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    running+=("${name}:$(cat "$pid_file")")
  fi
done

if (( ${#running[@]} > 0 )) && [[ "${FORCE_CLEAN_RUNNING:-0}" != "1" ]]; then
  echo "clean refused: local services appear to be running (${running[*]})." >&2
  echo "Run 'make down' first, or set FORCE_CLEAN_RUNNING=1 if you intentionally want to remove tracking files while processes may still run." >&2
  exit 2
fi

remove_path() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "would remove: $path"
  else
    rm -rf "$path"
  fi
}

find_project_artifacts() {
  # cache 탐색은 실행 가능한 repository code 영역으로 한정한다. venv, 모델
  # 데이터, runtime state와 로컬 도구 디렉터리를 음수 목록으로 순회하지 않는다.
  local roots=()
  local relative
  for relative in src scripts tests ops; do
    [[ -d "$ROOT/$relative" ]] && roots+=("$ROOT/$relative")
  done
  (( ${#roots[@]} > 0 )) || return 0
  find "${roots[@]}" "$@"
}

remove_glob_find() {
  if [[ "$DRY_RUN" == "1" ]]; then
    find_project_artifacts -type d -name __pycache__ -prune -print | sed 's/^/would remove: /'
    find_project_artifacts -type d -name '*.egg-info' -prune -print | sed 's/^/would remove: /'
    find_project_artifacts \
      \( -type d -name __pycache__ \) -prune -o \
      -type f -name '*.pyc' -print | sed 's/^/would remove: /'
  else
    find_project_artifacts -type d -name __pycache__ -prune -exec rm -rf {} +
    find_project_artifacts -type d -name '*.egg-info' -prune -exec rm -rf {} +
    find_project_artifacts \
      \( -type d -name __pycache__ \) -prune -o \
      -type f -name '*.pyc' -delete
  fi
}

remove_os_metadata() {
  local roots=("$ROOT")
  local relative
  for relative in assets configs docs ops scripts specs src tests; do
    [[ -d "$ROOT/$relative" ]] && roots+=("$ROOT/$relative")
  done
  if [[ "$DRY_RUN" == "1" ]]; then
    find "${roots[@]}" -maxdepth 1 -type f -name .DS_Store -print |
      sort -u | sed 's/^/would remove: /'
  else
    find "${roots[@]}" -maxdepth 1 -type f -name .DS_Store -delete
  fi
}

remove_empty_dir() {
  local path="$1"
  [[ -d "$path" ]] || return 0
  [[ -z "$(find "$path" -mindepth 1 -print -quit)" ]] || return 0
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "would remove empty directory: $path"
  else
    rmdir "$path"
  fi
}

remove_runtime_validation_reports() {
  local report_dir="$ROOT/reports/runtime"
  [[ -d "$report_dir" ]] || return 0
  if [[ "$DRY_RUN" == "1" ]]; then
    find "$report_dir" -maxdepth 1 -type f \
      \( -name 'runtime_validation_*.json' -o -name 'runtime_validation_*.md' \) \
      -print | sed 's/^/would remove: /'
  else
    find "$report_dir" -maxdepth 1 -type f \
      \( -name 'runtime_validation_*.json' -o -name 'runtime_validation_*.md' \) \
      -delete
  fi
}

for path in \
  "$ROOT/dist" "$ROOT/build" "$ROOT/outputs" "$ROOT/run" \
  "$ROOT/.pytest_cache" "$ROOT/.mypy_cache" "$ROOT/.ruff_cache" \
  "$ROOT/.coverage" "$ROOT/coverage.xml" "$ROOT/htmlcov"; do
  remove_path "$path"
done
remove_glob_find
remove_os_metadata
remove_runtime_validation_reports
remove_empty_dir "$ROOT/reports/runtime"
remove_empty_dir "$ROOT/reports"

if [[ "$INCLUDE_LOGS" == "1" ]]; then
  remove_path "$ROOT/logs"
  # native runtime 로그도 같은 의미의 로그다. 위치만 runtime state 아래에 있다.
  remove_path "$ROOT/.runtime/metal/logs"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "dry run complete: listed paths would be removed; .env, environments, runtime state, images, and model caches kept."
elif [[ "$INCLUDE_LOGS" == "1" ]]; then
  echo "clean complete: generated artifacts, runtime validation reports, and logs removed when present."
else
  echo "clean complete: generated artifacts and runtime validation reports removed when present; logs kept (use LOGS=1 to include them)."
fi
