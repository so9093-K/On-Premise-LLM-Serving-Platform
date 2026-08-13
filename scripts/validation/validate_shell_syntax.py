#!/usr/bin/env python3
"""저장소의 모든 셸 스크립트가 `bash -n`으로 구문 오류 없이 파싱되는지 검증한다.

체크 항목:
- scripts/**/*.sh, ops/**/*.sh 전체가 유효한 bash 구문인지.
- scripts/ci/deploy_gitlab_compose.sh 안에 내장된 `bash -s <<'REMOTE' ... REMOTE`
  블록은 따로 추출해서 별도로 구문 검사한다 -- 바깥 파일 자체의 `bash -n`은
  따옴표 있는 heredoc 본문 안쪽을 절대 파싱하지 않으므로, 안 그러면 그 안의
  구문 오류가 실제 원격 배포가 실행될 때까지 안 보인다.

사용법:
  python scripts/validation/validate_shell_syntax.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REMOTE_HEREDOC_START = "<<'REMOTE'"
REMOTE_HEREDOC_END = "REMOTE"


def _bash_n(label: str, script_text: str) -> str | None:
    result = subprocess.run(
        ["bash", "-n"],
        input=script_text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"{label}: {result.stderr.strip()}"
    return None


def _extract_remote_heredoc(path: Path) -> str | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    in_heredoc = False
    for line in lines:
        if not in_heredoc:
            if REMOTE_HEREDOC_START in line:
                in_heredoc = True
            continue
        if line == REMOTE_HEREDOC_END:
            return "\n".join(body)
        body.append(line)
    return None


def validate(root: Path) -> list[str]:
    violations: list[str] = []
    sh_files = sorted({*root.glob("scripts/**/*.sh"), *root.glob("ops/**/*.sh")})
    for path in sh_files:
        rel = path.relative_to(root)
        error = _bash_n(str(rel), path.read_text(encoding="utf-8"))
        if error:
            violations.append(error)

    deploy_script = root / "scripts/ci/deploy_gitlab_compose.sh"
    if deploy_script.exists():
        remote_block = _extract_remote_heredoc(deploy_script)
        if remote_block is None:
            violations.append(
                f"{deploy_script.relative_to(root)}: expected a `bash -s {REMOTE_HEREDOC_START} ... {REMOTE_HEREDOC_END}` "
                "block but none was found -- update this validator if the remote-execution mechanism changed."
            )
        else:
            error = _bash_n(f"{deploy_script.relative_to(root)} (REMOTE heredoc)", remote_block)
            if error:
                violations.append(error)

    return violations


def main() -> int:
    violations = validate(ROOT)

    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\nvalidate_shell_syntax: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1

    print("validate_shell_syntax: OK — all shell scripts parse cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
