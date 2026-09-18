#!/usr/bin/env bash
# Canonical Runtime Startup Profile process input normalization.
#
# Usage:
#   normalize_runtime_startup_profile RUNTIME_PROFILE
#   normalize_runtime_startup_profile DEPLOY_RUNTIME_PROFILE
#
# RUNTIME_STARTUP_PROFILE is canonical. Legacy variable names are read-only aliases.
normalize_runtime_startup_profile() {
  local selected="${RUNTIME_STARTUP_PROFILE:-}"
  local legacy_name legacy_value

  for legacy_name in "$@"; do
    legacy_value="${!legacy_name:-}"
    [[ -n "${legacy_value}" ]] || continue
    if [[ -n "${selected}" && "${selected}" != "${legacy_value}" ]]; then
      echo "[runtime-startup-profile] ERROR: RUNTIME_STARTUP_PROFILE conflicts with legacy ${legacy_name}." >&2
      return 2
    fi
    selected="${legacy_value}"
  done

  RUNTIME_STARTUP_PROFILE="${selected}"
  export RUNTIME_STARTUP_PROFILE
}
