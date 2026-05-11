from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

DOC_PATHS = [
    ROOT / "README.md",
    ROOT / "docs/README.md",
    ROOT / "docs/operations/auth_control_plane.md",
    ROOT / "docs/operations/risk_vllm_patch_lifecycle.md",
    ROOT / "docs/operations/project_state_maintainability_review.md",
    ROOT / "reports/refactor/current_handoff_summary.md",
    ROOT / "reports/refactor/current_refactor_state.md",
    ROOT / "reports/runtime/operator_status_bundle.md",
    ROOT / "reports/runtime/monitoring_projection.md",
]


def _korean_ratio(text: str) -> float:
    korean = sum(1 for ch in text if "가" <= ch <= "힣")
    letters = sum(1 for ch in text if ch.isalpha())
    return korean / letters if letters else 0.0


def test_primary_docs_are_korean_first_without_parallel_language_split() -> None:
    forbidden = ["한국어버전", "한국어 버전", "영어버전", "영어 버전", "Korean version", "English version"]
    for path in DOC_PATHS:
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        assert _korean_ratio(text) >= 0.10, path
        for phrase in forbidden:
            assert phrase not in text, f"{path} contains parallel-language split phrase: {phrase}"


def test_generated_operator_reports_have_korean_headings() -> None:
    expectations = {
        "reports/runtime/runtime_targets.md": "# 런타임 대상 인벤토리",
        "reports/runtime/operator_status_bundle.md": "# 운영 상태 번들",
        "reports/runtime/monitoring_projection.md": "# 모니터링 Projection",
        "reports/runtime/storage_paths.md": "# 로컬 저장소 경로 리포트",
        "reports/refactor/project_inventory_current.md": "# 프로젝트 인벤토리와 파일 검토",
    }
    for rel, heading in expectations.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert heading in text
        assert "## 운영 해석" in text or "## 관리 해석" in text

def test_operator_visible_specs_env_and_cli_help_do_not_regress_to_english_first() -> None:
    checked = [
        ROOT / "specs/openapi.gateway.yaml",
        ROOT / "specs/openapi.risk-adapter.yaml",
        ROOT / ".env.example",
        ROOT / ".env.local.example",
        ROOT / ".env.compose.example",
        ROOT / "scripts/config/setup_env.py",
        ROOT / "scripts/auth/auth_status.py",
        ROOT / "scripts/auth/auth_doctor.py",
        ROOT / "scripts/auth/auth_apply.py",
        ROOT / "version_manifest.json",
    ]
    forbidden = [
        "Logical model catalog",
        "OpenAI-compatible chat completions",
        "Unauthorized",
        "Validation error",
        "Runtime unavailable",
        "Signal-only risk assessment",
        "Admin bearer token required",
        "Override APP_ENV",
        "Output env path",
        "Monitoring UX reference",
    ]
    for path in checked:
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{path} contains English-first operator phrase: {phrase}"




def test_primary_cli_help_uses_korean_argparse_labels() -> None:
    for rel in ["scripts/auth/auth_status.py", "scripts/auth/auth_doctor.py", "scripts/config/setup_env.py", "scripts/models/modelctl.py"]:
        result = subprocess.run([sys.executable, rel, "--help"], cwd=ROOT, check=True, text=True, capture_output=True, timeout=10)
        assert "사용법:" in result.stdout, rel
        assert "옵션:" in result.stdout, rel
        assert "show this help message and exit" not in result.stdout, rel
