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
