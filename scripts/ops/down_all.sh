#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$ROOT"

# Host processes are repository-owned through PID files and can be stopped even
# when .env or Docker is unavailable.
bash scripts/ops/down_services.sh --local

metal_pid_file="$ROOT/run/metal.pid"
if [[ -f "$metal_pid_file" ]]; then
  metal_pid="$(cat "$metal_pid_file")"
  if kill -0 "$metal_pid" >/dev/null 2>&1; then
    echo "[down-all] stopping managed Metal runtime pid ${metal_pid}"
    kill "$metal_pid"
    for _ in $(seq 1 120); do
      kill -0 "$metal_pid" >/dev/null 2>&1 || break
      sleep 0.25
    done
    if kill -0 "$metal_pid" >/dev/null 2>&1; then
      echo "[down-all] Metal runtime pid ${metal_pid} did not stop; inspect it before retrying" >&2
      exit 2
    fi
  fi
  rm -f "$metal_pid_file"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[down-all] Docker CLI unavailable; host processes stopped, Compose ownership cannot be checked" >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "[down-all] Docker daemon unavailable; host processes stopped, Compose resources were not changed" >&2
  exit 2
fi

# Compose project names are operator-configurable, so discover ownership from
# Docker's exact working-directory label instead of duplicating names here.
container_ids="$(
  docker ps -aq \
    --filter "label=com.docker.compose.project.working_dir=${ROOT}" 2>/dev/null || true
)"
if [[ -z "$container_ids" ]]; then
  echo "[down-all] no Compose containers owned by this checkout"
  exit 0
fi

projects=""
for container_id in $container_ids; do
  project="$(
    docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' \
      "$container_id" 2>/dev/null || true
  )"
  if [[ -n "$project" ]]; then
    projects="${projects}${project}"$'\n'
  fi
done

echo "[down-all] stopping Compose containers owned by ${ROOT}"
docker stop $container_ids >/dev/null
docker rm $container_ids >/dev/null

while IFS= read -r project; do
  [[ -n "$project" ]] || continue
  network_ids="$(
    docker network ls -q --filter "label=com.docker.compose.project=${project}" 2>/dev/null || true
  )"
  if [[ -n "$network_ids" ]]; then
    docker network rm $network_ids >/dev/null
  fi
done < <(printf '%s' "$projects" | sort -u)

echo "[down-all] stopped all resources owned by this checkout; images, volumes, and model caches kept"
