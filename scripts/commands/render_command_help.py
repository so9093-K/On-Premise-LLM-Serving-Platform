#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "configs" / "command_registry.yaml"


def _load_registry() -> dict:
    try:
        return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"command_registry.yaml 로드 실패: {exc}") from exc


def _public_commands(registry: dict) -> list[dict]:
    return [c for c in registry["commands"] if c.get("kind") not in ("internal",)]


def _commands_by_category(registry: dict, commands: list[dict]) -> list[tuple[dict, list[dict]]]:
    category_map = {cat["id"]: cat for cat in registry["categories"]}
    seen_ids = [cat["id"] for cat in registry["categories"]]
    buckets: dict[str, list[dict]] = {cid: [] for cid in seen_ids}
    for cmd in commands:
        cid = cmd.get("category", "")
        if cid in buckets:
            buckets[cid].append(cmd)
    return [(category_map[cid], buckets[cid]) for cid in seen_ids if buckets[cid]]


def _render_compact(registry: dict) -> str:
    version_file = ROOT / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "0.0.0"
    project = "ai_model_serving_platform"

    lines: list[str] = [
        f"{project} {version}",
        "",
        "어디서 시작할지 모르면: docs/START_HERE.md",
        "상세 가이드: docs/README.md",
        "상황별 명령 추천: make guide",
        "전체 공개 명령: make help-full",
        "",
    ]
    commands = [c for c in registry["commands"] if c.get("kind") == "public"]
    for cat, cmds in _commands_by_category(registry, commands):
        if not cmds:
            continue
        lines.append(f"── {cat['title']} {'─' * max(1, 50 - len(cat['title']))}")
        for cmd in cmds:
            target = cmd["make_target"]
            desc = cmd.get("description", "").split("。")[0].split(". ")[0].rstrip(".")
            lines.append(f"make {target:<30} # {desc}")
        lines.append("")
    return "\n".join(lines)


def _render_full(registry: dict) -> str:
    version_file = ROOT / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "0.0.0"
    project = "ai_model_serving_platform"

    lines: list[str] = [
        f"# {project} {version} — 전체 명령 레퍼런스",
        "",
    ]
    public = [c for c in registry["commands"] if c.get("kind") == "public"]
    alias = [c for c in registry["commands"] if c.get("kind") == "alias"]
    deprecated = [c for c in registry["commands"] if c.get("kind") == "deprecated"]

    for cat, cmds in _commands_by_category(registry, public):
        if not cmds:
            continue
        lines.append(f"## {cat['title']}")
        lines.append("")
        for cmd in cmds:
            audience = ", ".join(cmd.get("audience", []))
            safety = cmd.get("safety", "")
            requires = cmd.get("requires", {})
            req_tags = [k for k, v in requires.items() if v]
            lines.append(f"### `make {cmd['make_target']}`")
            lines.append(f"{cmd.get('description', '')}")
            lines.append(f"- **audience:** {audience}")
            lines.append(f"- **safety:** {safety}")
            if req_tags:
                lines.append(f"- **requires:** {', '.join(req_tags)}")
            docs = cmd.get("docs", [])
            if docs:
                lines.append(f"- **docs:** {', '.join(docs)}")
            lines.append("")

    if alias:
        lines.append("## 별칭 (alias)")
        lines.append("")
        for cmd in alias:
            lines.append(f"- `make {cmd['make_target']}` — {cmd.get('description', '')}")
        lines.append("")

    if deprecated:
        lines.append("## 폐기 (deprecated)")
        lines.append("")
        for cmd in deprecated:
            replacement = cmd.get("replacement", "")
            suffix = f" → `{replacement}` 사용" if replacement else ""
            lines.append(f"- `make {cmd['make_target']}`{suffix} — {cmd.get('description', '')}")
        lines.append("")

    return "\n".join(lines)


def _render_json(registry: dict) -> str:
    public = _public_commands(registry)
    output = {
        "schema_version": registry.get("schema_version", 1),
        "categories": registry["categories"],
        "commands": public,
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="command_registry.yaml 기반 help를 렌더링합니다.")
    parser.add_argument("--mode", choices=["compact", "full"], default="compact")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON 출력")
    args = parser.parse_args()

    registry = _load_registry()

    if args.json:
        print(_render_json(registry))
    elif args.mode == "full":
        print(_render_full(registry))
    else:
        print(_render_compact(registry))
    return 0


if __name__ == "__main__":
    sys.exit(main())
