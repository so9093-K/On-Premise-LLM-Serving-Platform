#!/usr/bin/env bash
# Deploy recreate 판단의 순수 정책 함수. 호출자는 현재 release 디렉터리에서 실행한다.

deploy_runtime_config_changed() {
  local baseline="$1" current="$2" rel
  [[ -n "$baseline" && -d "$baseline" ]] || return 1
  for rel in configs/main_model_profiles.yaml configs/gemma4_chat_template.jinja; do
    cmp -s "$baseline/$rel" "$current/$rel" 2>/dev/null || return 0
  done
  return 1
}

deploy_compose_config_changed() {
  local baseline="$1" current="$2" compose_file="$3"
  [[ -n "$baseline" && -d "$baseline" ]] || return 1
  ! cmp -s "$baseline/$compose_file" "$current/$compose_file" 2>/dev/null
}

deploy_changed_files() {
  local baseline="$1" current="$2"
  shift 2
  local rel
  [[ -n "$baseline" && -d "$baseline" ]] || return 0
  for rel in "$@"; do
    cmp -s "$baseline/$rel" "$current/$rel" 2>/dev/null || printf '%s\n' "$rel"
  done
}

deploy_has_fresh_unified_image_artifact() {
  # 새 unified 이미지가 이번 pipeline의 build artifact에서 왔는지 호출자가 보장한다.
  # 여기서는 mutable tag/current .env 값과 구분하기 위해 immutable digest 모양만
  # 허용한다. Dockerfile·patch 변경 배포에서 기존 이미지 ref를 이 값으로 넘겨
  # stale-image 가드를 우회하지 않게 하는 최소 정책이다.
  local image_ref="${1:-}"
  [[ "${image_ref}" =~ @sha256:[0-9a-f]{64}$ ]]
}

deploy_unified_image_config_changed() {
  # unified Docker build의 단일 설정만 비교한다. 일반 runtime image tag 변경이
  # 비싼 vLLM 재빌드를 강제하지 않게 하면서, base/pin drift는 놓치지 않는다.
  local baseline="$1" current="$2" before_path after_path python_bin
  [[ -n "$baseline" && -d "$baseline" ]] || return 1
  before_path="$baseline/configs/vllm_unified_build.yaml"
  after_path="$current/configs/vllm_unified_build.yaml"
  # 이 파일을 새 source-of-truth로 도입한 첫 배포에서는 이전 release에만 파일이
  # 없다. 이는 비교 불능 오류가 아니라 build 입력 변경이다. 호출자의 fresh-artifact
  # guard가 새 derived digest를 요구하도록 changed(0)로 돌려준다.
  [[ -f "$before_path" ]] || return 0
  if [[ ! -f "$after_path" ]]; then
    echo "[deploy] ERROR: current vLLM unified image configuration is missing: $after_path" >&2
    return 2
  fi
  python_bin="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
  "$python_bin" - "$before_path" "$after_path" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def relevant(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("configuration root must be an object")
    return {
        "base_image_default": document.get("base_image_default"),
        "compatibility_pins": document.get("compatibility_pins"),
    }


try:
    before, after = (relevant(Path(value)) for value in sys.argv[1:3])
except Exception as exc:
    print(f"[deploy] ERROR: cannot compare vLLM unified image configuration: {exc}", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0 if before != after else 1)
PY
}
