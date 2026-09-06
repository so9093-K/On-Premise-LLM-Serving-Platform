#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$ROOT"

PYTHON_VERSION="$(tr -d '[:space:]' < .python-version)"
PYTHON_IMAGE="$(sed -n 's/^FROM \(python:[^[:space:]]*\)$/\1/p' Dockerfile | head -n 1)"
EXPECTED_PREFIX="python:${PYTHON_VERSION}-slim@sha256:"
case "$PYTHON_IMAGE" in
  "$EXPECTED_PREFIX"*) ;;
  *)
    echo "[lock-linux] Dockerfile must declare the digest-pinned Python ${PYTHON_VERSION} image" >&2
    exit 2
    ;;
esac

if [ "${LOCK_LINUX_CONTAINER:-0}" != "1" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "[lock-linux] Docker CLI is required" >&2
    exit 2
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "[lock-linux] cannot access the Docker daemon" >&2
    exit 2
  fi
  exec docker run --rm \
    --platform linux/amd64 \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env LOCK_LINUX_CONTAINER=1 \
    --env CUSTOM_COMPILE_COMMAND="make lock-linux" \
    --volume "$ROOT:/workspace" \
    --workdir /workspace \
    "$PYTHON_IMAGE" \
    sh scripts/build/refresh_dependency_locks.sh
fi

if [ "$(uname -s)/$(uname -m)" != "Linux/x86_64" ]; then
  echo "[lock-linux] resolver container must run as Linux/x86_64" >&2
  exit 2
fi

ACTUAL_VERSION="$(python -c 'import platform; print(platform.python_version())')"
if [ "$ACTUAL_VERSION" != "$PYTHON_VERSION" ]; then
  echo "[lock-linux] expected Python ${PYTHON_VERSION}, got ${ACTUAL_VERSION}" >&2
  exit 2
fi

WORK="$(mktemp -d)"
LOCKS_REPLACED=0
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ] && [ "$LOCKS_REPLACED" = "1" ]; then
    cp "$WORK/requirements.lock.original" requirements.lock
    cp "$WORK/requirements.runtime.lock.original" requirements.runtime.lock
    echo "[lock-linux] restored the previous lock files after validation failure" >&2
  fi
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

cp requirements.lock "$WORK/requirements.lock.original"
cp requirements.runtime.lock "$WORK/requirements.runtime.lock.original"
# pip-compile은 기존 output을 constraint로 재사용한다. 현재 pin을 임시
# output에 seed해 lock 형식 재생성과 전이 의존성 업그레이드를 분리한다.
cp requirements.lock "$WORK/requirements.lock"
cp requirements.runtime.lock "$WORK/requirements.runtime.lock"

python -m venv "$WORK/tools"
"$WORK/tools/bin/python" -m pip install --disable-pip-version-check \
  'pip==26.0.1' 'pip-tools==7.5.3'

"$WORK/tools/bin/pip-compile" \
  --resolver=backtracking \
  --strip-extras \
  --no-emit-index-url \
  --no-emit-trusted-host \
  --output-file "$WORK/requirements.runtime.lock" \
  pyproject.toml
"$WORK/tools/bin/pip-compile" \
  --resolver=backtracking \
  --strip-extras \
  --no-emit-index-url \
  --no-emit-trusted-host \
  --extra contract \
  --output-file "$WORK/requirements.lock" \
  pyproject.toml

verify_lock() {
  name="$1"
  lock="$2"
  environment="$WORK/$name"
  python -m venv "$environment"
  "$environment/bin/python" -m pip install --disable-pip-version-check 'pip==26.0.1'
  "$environment/bin/python" -m pip install --disable-pip-version-check --requirement "$lock"
  "$environment/bin/python" -m pip install --disable-pip-version-check 'setuptools==83.0.0'
  "$environment/bin/python" -m pip install --disable-pip-version-check \
    --no-deps --no-build-isolation .
  "$environment/bin/python" -m pip check
}

verify_lock runtime-check "$WORK/requirements.runtime.lock"
verify_lock contract-check "$WORK/requirements.lock"

cp "$WORK/requirements.runtime.lock" requirements.runtime.lock
cp "$WORK/requirements.lock" requirements.lock
LOCKS_REPLACED=1

"$WORK/contract-check/bin/python" scripts/validation/validate_contracts.py
LOCKS_REPLACED=0
echo "[lock-linux] regenerated and verified requirements.runtime.lock and requirements.lock"
