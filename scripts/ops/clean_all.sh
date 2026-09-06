#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="${1:-}"
DRY_RUN=0
if [[ "$MODE" == "--dry-run" ]]; then
  DRY_RUN=1
  MODE=""
elif [[ "$MODE" == "--all-dry-run" ]]; then
  DRY_RUN=1
  MODE="--all"
fi

running=()
for name in gateway risk_adapter; do
  pid_file="$ROOT/run/${name}.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
    running+=("${name}:$(cat "$pid_file")")
  fi
done

if (( ${#running[@]} > 0 )) && [[ "${FORCE_CLEAN_RUNNING:-0}" != "1" ]]; then
  echo "clean refused: local services appear to be running (${running[*]})." >&2
  echo "Run 'make stop' first, or set FORCE_CLEAN_RUNNING=1 if you intentionally want to remove tracking files while processes may still run." >&2
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
remove_runtime_validation_reports
remove_empty_dir "$ROOT/reports/runtime"
remove_empty_dir "$ROOT/reports"

if [[ "$MODE" == "--all" ]]; then
  remove_path "$ROOT/logs"
  cleaned="generated artifacts and logs"
  notes=()
  if [[ "${PURGE_MODEL_CACHE:-0}" == "1" ]]; then
    remove_path "$ROOT/model_cache"
    remove_path "$ROOT/ops/compose/model_cache"
    remove_path "$ROOT/models"
    cleaned="$cleaned, model caches"
  else
    notes+=("model_cache/models kept; set PURGE_MODEL_CACHE=1 for destructive cache purge")
  fi
  if [[ "${PURGE_RUNTIME_SECRETS:-0}" == "1" ]]; then
    remove_path "$ROOT/.runtime"
    cleaned="$cleaned, runtime secrets"
  else
    notes+=(".runtime kept; set PURGE_RUNTIME_SECRETS=1 only when intentionally regenerating local runtime secrets")
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'dry run complete: listed paths would be removed'
  else
    printf 'clean complete: %s removed when present' "$cleaned"
  fi
  for note in "${notes[@]}"; do
    printf '; %s' "$note"
  done
  printf '\n'
else
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "dry run complete: listed paths would be removed; logs, .runtime, and model_cache/models kept."
  else
    echo "clean complete: generated artifacts and timestamped runtime validation reports removed when present; logs, .runtime, and model_cache/models kept. Use make clean-all to remove logs."
  fi
fi
