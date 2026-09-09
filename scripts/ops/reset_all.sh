#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$ROOT"
source scripts/lib/project_image_ownership.sh

print_plan() {
  cat <<'EOF'
[reset] project-local reset plan
  stop/remove: host processes and Compose containers/networks owned by this checkout
  remove images: images built by this project (project label or canonical local repository)
  remove files: .env, .venv, .runtime, logs, run, build/test artifacts,
                repository-local model_cache and models
  preserve: Docker volumes, daemon-wide BuildKit cache, registry images without
            this project's build label, global Hugging Face cache, and unrelated
            Docker resources

Nothing has been removed. Apply exactly with:
  make reset CONFIRM=reset
EOF
}

if [[ "$#" -ne 2 || "${1:-}" != "--confirm" || "${2:-}" != "reset" ]]; then
  print_plan
  exit 0
fi

# Do not erase local configuration when Docker resources cannot first be
# inspected and stopped. This prevents an unverifiable partial reset.
if ! command -v docker >/dev/null 2>&1; then
  echo "[reset] Docker CLI is required to verify project-owned resources before reset" >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "[reset] Docker daemon is unavailable; no reset action was started" >&2
  exit 2
fi

bash scripts/ops/down_all.sh

image_ids="$(
  {
    docker image ls -q \
      --filter "label=${PROJECT_IMAGE_LABEL_KEY}=${PROJECT_IMAGE_LABEL_VALUE}"
    docker image ls -q "$PROJECT_PLATFORM_IMAGE_REPOSITORY"
    docker image ls -q "$PROJECT_VLLM_IMAGE_REPOSITORY"
  } 2>/dev/null | sort -u
)"
image_refs="$(
  {
    docker image ls \
      --filter "label=${PROJECT_IMAGE_LABEL_KEY}=${PROJECT_IMAGE_LABEL_VALUE}" \
      --format '{{.Repository}}:{{.Tag}}'
    docker image ls "$PROJECT_PLATFORM_IMAGE_REPOSITORY" \
      --format '{{.Repository}}:{{.Tag}}'
    docker image ls "$PROJECT_VLLM_IMAGE_REPOSITORY" \
      --format '{{.Repository}}:{{.Tag}}'
  } 2>/dev/null | grep -v '^<none>:' | sort -u || true
)"
if [[ -n "$image_refs" ]]; then
  echo "[reset] removing project-built image tags"
  docker image rm $image_refs
fi
remaining_image_ids=""
for image_id in $image_ids; do
  if docker image inspect "$image_id" >/dev/null 2>&1; then
    remaining_image_ids="${remaining_image_ids} ${image_id}"
  fi
done
if [[ -n "$remaining_image_ids" ]]; then
  echo "[reset] removing untagged project-built images"
  docker image rm $remaining_image_ids
elif [[ -z "$image_refs" ]]; then
  echo "[reset] no project-built images found"
else
  echo "[reset] project-built images removed"
fi

FORCE_CLEAN_RUNNING=1 bash scripts/ops/clean_project.sh --logs

for path in \
  "$ROOT/.env" \
  "$ROOT/.venv" \
  "$ROOT/.runtime" \
  "$ROOT/model_cache" \
  "$ROOT/ops/compose/model_cache" \
  "$ROOT/models"; do
  if [[ -e "$path" || -L "$path" ]]; then
    echo "[reset] removing ${path#$ROOT/}"
    rm -rf "$path"
  fi
done

echo "[reset] complete; Docker build cache, global Hugging Face cache, and unrelated Docker state were preserved"
echo "[reset] next: make setup TARGET=<deployment-target>"
