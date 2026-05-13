#!/usr/bin/env python3
"""check_docs_links: Markdown 상대 링크 유효성 검사.

README.md, docs/**, reports/**, scripts/README.md의 상대 링크를 검사한다.
timestamp-dependent 링크 패턴은 예외 처리한다.
make docs-check에서 호출한다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]

SCAN_PATTERNS = [
    "README.md",
    "docs/**/*.md",
    "scripts/README.md",
    "reports/runtime/README.md",
    "reports/refactor/*.md",
]

TIMESTAMP_PATTERN = re.compile(r"runtime_validation_\d{8}T\d{6}Z")
LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
ANCHOR_PATTERN = re.compile(r"^#")


def _is_external(href: str) -> bool:
    parsed = urlparse(href)
    return bool(parsed.scheme in {"http", "https", "mailto"})


def _is_timestamp_link(href: str) -> bool:
    return bool(TIMESTAMP_PATTERN.search(href))


def _anchor_exists(target_file: Path, anchor: str) -> bool:
    """GitHub-style anchor check: lowercase, spaces→dash, strip special chars."""
    if not target_file.exists():
        return False
    text = target_file.read_text(encoding="utf-8")
    heading_pattern = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
    for match in heading_pattern.finditer(text):
        heading_text = match.group(1).strip()
        slug = heading_text.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r" ", "-", slug)
        slug = slug.strip("-")
        if slug == anchor.lstrip("#"):
            return True
    return False


def check_file(md_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return [f"{md_path}: 읽기 실패"]

    for match in LINK_PATTERN.finditer(text):
        href = match.group(2).strip()
        if _is_external(href):
            continue
        if _is_timestamp_link(href):
            continue
        if href.startswith("#"):
            anchor = href
            if not _anchor_exists(md_path, anchor):
                errors.append(f"{md_path.relative_to(ROOT)}: anchor not found: {href}")
            continue

        parts = href.split("#", 1)
        rel_path = parts[0]
        anchor = "#" + parts[1] if len(parts) > 1 else None

        if not rel_path:
            continue

        target = (md_path.parent / rel_path).resolve()
        if not target.exists():
            errors.append(f"{md_path.relative_to(ROOT)}: broken link → {href}")
            continue

        if anchor and target.suffix == ".md":
            if not _anchor_exists(target, anchor):
                errors.append(f"{md_path.relative_to(ROOT)}: anchor not found in {rel_path}: {anchor}")

    return errors


def main() -> int:
    all_errors: list[str] = []
    checked = 0

    for pattern in SCAN_PATTERNS:
        if "**" in pattern or "*" in pattern:
            for path in ROOT.glob(pattern):
                if path.is_file() and path.suffix == ".md":
                    errors = check_file(path)
                    all_errors.extend(errors)
                    checked += 1
        else:
            path = ROOT / pattern
            if path.exists() and path.is_file():
                errors = check_file(path)
                all_errors.extend(errors)
                checked += 1

    if all_errors:
        print(f"[check_docs_links] {len(all_errors)}개 링크 오류 (검사 파일: {checked}개)")
        for error in all_errors:
            print(f"  ✗ {error}")
        return 1

    print(f"[check_docs_links] 링크 검사 통과 (검사 파일: {checked}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
