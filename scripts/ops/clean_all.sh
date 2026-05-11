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
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "would remove: $path"
  else
    rm -rf "$path"
  fi
}

remove_glob_find() {
  if [[ "$DRY_RUN" == "1" ]]; then
    find "$ROOT" -type d -name __pycache__ -prune -print | sed 's/^/would remove: /' || true
    find "$ROOT" -type d -name '*.egg-info' -prune -print | sed 's/^/would remove: /' || true
    find "$ROOT" -type f -name '*.pyc' -print | sed 's/^/would remove: /' || true
  else
    find "$ROOT" -type d -name __pycache__ -prune -exec rm -rf {} +
    find "$ROOT" -type d -name '*.egg-info' -prune -exec rm -rf {} +
    find "$ROOT" -type f -name '*.pyc' -delete
  fi
}

for path in "$ROOT/dist" "$ROOT/build" "$ROOT/outputs" "$ROOT/run" "$ROOT/.pytest_cache"; do
  remove_path "$path"
done
remove_glob_find

if [[ "$MODE" == "--all" ]]; then
  remove_path "$ROOT/logs"
  removed="removed generated artifacts and logs"
  if [[ "${PURGE_MODEL_CACHE:-0}" == "1" ]]; then
    remove_path "$ROOT/model_cache"
    remove_path "$ROOT/ops/compose/model_cache"
    remove_path "$ROOT/models"
    removed="$removed, model caches"
  else
    removed="$removed; model_cache/models kept. Set PURGE_MODEL_CACHE=1 for destructive cache purge"
  fi
  if [[ "${PURGE_RUNTIME_SECRETS:-0}" == "1" ]]; then
    remove_path "$ROOT/.runtime"
    removed="$removed, and runtime secrets"
  else
    removed="$removed; .runtime kept. Set PURGE_RUNTIME_SECRETS=1 only when you intend to regenerate local runtime secrets"
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "dry run: $removed"
  else
    echo "$removed"
  fi
else
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "dry run: generated artifacts would be removed; logs, .runtime, and model_cache/models kept."
  else
    echo "removed generated artifacts; logs, .runtime, and model_cache/models kept. Use make clean-all to remove logs."
  fi
fi
