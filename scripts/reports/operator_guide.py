#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:
    import yaml as _yaml
    _REGISTRY_PATH = Path(__file__).resolve().parents[2] / "configs" / "command_registry.yaml"

    def _registry_descriptions() -> dict[str, str]:
        """command_registry.yaml에서 make_target → description 맵을 반환한다."""
        if not _REGISTRY_PATH.exists():
            return {}
        data = _yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return {cmd["make_target"]: cmd.get("description", "") for cmd in data.get("commands", [])}

except ImportError:
    def _registry_descriptions() -> dict[str, str]:  # type: ignore[misc]
        return {}


@dataclass(frozen=True)
class Workflow:
    key: str
    title: str
    when: str
    commands: list[str]
    notes: list[str]


WORKFLOWS: list[Workflow] = [
    Workflow(
        key="local",
        title="Local app-only 개발",
        when="GPU/vLLM 없이 Gateway와 Risk Adapter process, /health, API docs만 확인한다.",
        commands=[
            "make init-env-local",
            "make validate",
            "make test",
            "make start",
            "make ready-local",
            "make status",
            "make stop",
        ],
        notes=[
            "app-only 모드에서는 make ready 대신 make ready-local을 사용한다.",
            "make init-env-compose로 만든 .env는 compose 내부 hostname을 사용한다.",
        ],
    ),
    Workflow(
        key="full-stack",
        title="GPU/vLLM full-stack 검증",
        when="대상 GPU 서버에서 compose, enabled vLLM runtime 3개, Prometheus, Grafana까지 확인한다.",
        commands=[
            "HF_TOKEN=hf_xxx make first-run",
            "source .venv/bin/activate",
            "make compose-up",
            "make ready-full",
            "make runtime-validate",
            "make operator-reports",
            "make compose-down",
        ],
        notes=[
            "이미 .env에 HF_TOKEN이 있으면 HF_TOKEN=... 주입은 생략 가능하다.",
            "앱 코드만 반복 수정할 때는 SKIP_RISK_VLLM_IMAGE_BUILD=auto make rebuild-full을 사용한다.",
        ],
    ),
    Workflow(
        key="reports",
        title="운영 산출물 생성",
        when="서비스를 새로 띄우지 않고 registry 기반 runtime/monitoring/status/evidence 산출물을 갱신한다.",
        commands=[
            "make runtime-targets",
            "make storage-paths",
            "make project-inventory",
            "make monitoring-projection",
            "make operator-status",
            "make live-evidence",
        ],
        notes=[
            "같은 작업을 한 번에 실행하려면 make operator-reports를 사용한다.",
            "파일·문서·관리 inventory는 make project-inventory를 사용한다.",
            "model/cache/report/secret 경로를 확인하려면 make storage-paths를 사용한다.",
            "make live-evidence는 가장 최신 live runtime validation report를 우선 사용한다.",
        ],
    ),
    Workflow(
        key="release",
        title="릴리스 전 정적 gate",
        when="서비스 기동 없이 계약, 설정, projection, evidence bundle을 검증한다.",
        commands=[
            "make release-check",
            "make release-check-full",
            "make package",
        ],
        notes=[
            "make release-check는 live GPU 검증을 대체하지 않는다.",
            "대상 GPU 서버에서는 별도로 make runtime-validate를 실행한다.",
        ],
    ),
    Workflow(
        key="cleanup",
        title="정리/초기화",
        when="로컬 산출물, 로그, 모델 캐시, runtime secret, Docker 이미지를 범위별로 정리한다.",
        commands=[
            "make remove-plan",
            "make clean",
            "make clean-all",
            "PURGE_MODEL_CACHE=1 make clean-all",
            "PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 PURGE_VENV=1 make reset",
        ],
        notes=[
            "make clean과 make clean-all은 Docker 이미지를 삭제하지 않는다.",
            "삭제 전 범위 확인은 make remove-plan, Docker 이미지 포함 통합 제거는 make reset을 사용한다.",
            ".runtime은 기본 보존된다. 재생성이 필요할 때만 PURGE_RUNTIME_SECRETS=1을 사용한다.",
        ],
    ),
]


def _select_workflows(keys: Iterable[str]) -> list[Workflow]:
    wanted = set(keys)
    if not wanted:
        return WORKFLOWS
    selected = [workflow for workflow in WORKFLOWS if workflow.key in wanted]
    missing = wanted - {workflow.key for workflow in selected}
    if missing:
        valid = ", ".join(workflow.key for workflow in WORKFLOWS)
        raise SystemExit(f"unknown workflow: {', '.join(sorted(missing))}. valid: {valid}")
    return selected


def render_markdown(workflows: list[Workflow]) -> str:
    desc_map = _registry_descriptions()
    lines = [
        "# Operator Workflow Guide",
        "",
        "상황별로 어떤 명령을 실행해야 하는지 빠르게 고르는 가이드다.",
        "전체 명령 목록은 `make help`, 처음 프로젝트 전체 흐름은 `docs/operations/first_project_guide.md`, 상세 문서는 `docs/README.md`를 기준으로 본다.",
        "",
    ]
    for workflow in workflows:
        lines.extend([
            f"## {workflow.title}",
            "",
            f"**언제:** {workflow.when}",
            "",
            "```bash",
        ])
        for cmd_str in workflow.commands:
            import re as _re
            m = _re.match(r"(?:.*\s)?make\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)", cmd_str)
            if m:
                target = m.group(1)
                registry_desc = desc_map.get(target, "")
                suffix = f"  # {registry_desc}" if registry_desc else ""
                lines.append(f"{cmd_str}{suffix}")
            else:
                lines.append(cmd_str)
        lines.extend([
            "```",
            "",
            "주의:",
        ])
        lines.extend(f"- {note}" for note in workflow.notes)
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="상황별 운영자 workflow 안내를 출력합니다.")
    parser.add_argument("workflow", nargs="*", help="선택 workflow key입니다: local, full-stack, reports, release, cleanup")
    parser.add_argument("--json", action="store_true", help="기계가 읽기 쉬운 workflow data를 출력합니다.")
    args = parser.parse_args()
    workflows = _select_workflows(args.workflow)
    if args.json:
        print(json.dumps([asdict(workflow) for workflow in workflows], ensure_ascii=False, indent=2))
    else:
        print(render_markdown(workflows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
