#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.auth_control import auth_profile_env_values  # noqa: E402
from scripts.config import setup_env  # noqa: E402

PROFILE_TO_TEMPLATE = {
    "local": ("local_open", ROOT / ".env.local.example"),
    "compose": ("local_open", ROOT / ".env.compose.example"),
}


def main() -> int:
    for profile, (mode, template_path) in PROFILE_TO_TEMPLATE.items():
        _, values = setup_env.parse_env_template(template_path)
        expected = auth_profile_env_values(mode)
        mismatches = {key: (values.get(key), expected_value) for key, expected_value in expected.items() if values.get(key) != expected_value}
        if mismatches:
            print(f"auth profile sanity failed for {profile} template: {mismatches}", file=sys.stderr)
            return 1
        app_env = values.get("APP_ENV", "")
        if app_env not in {"local", "test", "development"} and values.get("API_KEY_REQUIRED") != "true":
            print(f"auth profile sanity failed for {profile}: non-local APP_ENV={app_env} has API_KEY_REQUIRED!=true", file=sys.stderr)
            return 1
        print(f"auth profile sanity: {profile} template -> {mode} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
