from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# 사용자-facing 표시명에서 다시 사용하지 않을 legacy terminology.
# 안정 식별자(admin-sidecar, risk-adapter, DEPLOY_RUNTIME_PROFILE 등)는 소문자/코드
# 형태로 별도 계약이므로 이 목록의 대상이 아니다.
LEGACY_DISPLAY_TERMS: dict[str, str] = {
    "Admin Sidecar": "Runtime Controller",
    "Risk Adapter": "Risk Signal Service",
    "Secondary Runtime": "Model Runtime 또는 역할별 Runtime",
    "Deploy Runtime Profile": "Runtime Startup Profile",
    "Prompt Risk Runtime": "Prompt Injection Detector Runtime",
}


def _user_facing_paths(root: Path) -> list[Path]:
    paths = [
        root / "README.md",
        root / ".env.local.example",
        root / ".env.compose.example",
        root / "scripts" / "README.md",
        root / "src" / "ai_model_serving" / "api" / "endpoint_spec.py",
        root / "src" / "ai_model_serving" / "api_descriptions.py",
        root / "src" / "ai_model_serving" / "api_examples.py",
        root / "src" / "ai_model_serving" / "api" / "routers" / "gateway_runtime_control.py",
        root / "src" / "ai_model_serving" / "apps" / "risk_adapter.py",
    ]
    paths.extend(sorted((root / "docs").glob("*.md")))
    paths.extend(
        path
        for path in sorted((root / "docs" / "reference").glob("*.md"))
        if path.name != "terminology.md"
    )
    paths.extend(sorted((root / "ui" / "control-plane" / "src").glob("*.tsx")))
    return [path for path in paths if path.is_file()]


def terminology_violations(root: Path = ROOT) -> list[str]:
    violations: list[str] = []
    for path in _user_facing_paths(root):
        text = path.read_text(encoding="utf-8")
        for legacy, canonical in LEGACY_DISPLAY_TERMS.items():
            if legacy not in text:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if legacy in line:
                    violations.append(
                        f"{path.relative_to(root)}:{line_number}: legacy display term "
                        f"{legacy!r}; use {canonical!r}"
                    )
    return violations


def validate_canonical_terminology() -> None:
    violations = terminology_violations()
    if violations:
        raise SystemExit("\n".join(violations))
