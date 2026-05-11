from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .results import CheckResult


def write_reports(
    *,
    root: Path,
    output_dir: str,
    version: str,
    session_started: str,
    mode: str,
    results: list[CheckResult],
) -> tuple[Path, Path]:
    out_dir = root / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"runtime_validation_{stamp}.json"
    md_path = out_dir / f"runtime_validation_{stamp}.md"
    passed = sum(1 for item in results if item.passed)
    failed = len(results) - passed
    payload: dict[str, Any] = {
        "version": version,
        "started_at": session_started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "summary": {"passed": passed, "failed": failed},
        "results": [item.__dict__ for item in results],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = [
        "# 런타임 검증 리포트",
        "",
        f"모드: `{payload['mode']}`",
        f"통과: {passed}",
        f"실패: {failed}",
        "",
        "| Category | 검증 항목 | 상태 | Latency ms | 상세 |",
        "|---|---|---:|---:|---|",
    ]
    for item in results:
        rows.append(
            f"| {item.category} | {item.name} | {item.status} | "
            f"{item.latency_ms if item.latency_ms is not None else ''} | "
            f"{item.detail.replace('|', '/')} |"
        )
    try:
        json_label = str(json_path.relative_to(root))
    except ValueError:
        json_label = str(json_path)
    rows.extend([
        "",
        f"JSON 리포트: `{json_label}`",
        "",
        "이 검증 리포트에는 원문 프롬프트, 사용자 텍스트, 모델 출력이 기록되지 않는다.",
    ])
    md_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return json_path, md_path
