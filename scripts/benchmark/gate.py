"""릴리스 자격 검사에 붙는 성능 게이트(ADR-0026 10·14절).

`make runtime-validate`에 성능 판정을 합치지 않는다. 두 실패의 의미가 다르다.
기능이 깨진 것과 느려진 것은 다른 조치를 부른다.

이미 측정된 결과를 읽어 판정만 한다. 측정과 판정을 한 명령에 묶으면 판정을
다시 하려고 몇 분짜리 측정을 다시 돌려야 한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.benchmark.baseline import REGRESSION_PATH
from scripts.benchmark.contract import PerformanceContract, load_yaml_mapping

# ADR-0026 10절의 사다리. release 이상이 릴리스 자격을 실패시킨다.
_LADDER = ("observe", "warn", "release", "required")
_BLOCKING = {"release", "required"}


class GateError(RuntimeError):
    """판정 자체를 할 수 없는 상태."""


def _level(name: str) -> int:
    try:
        return _LADDER.index(name)
    except ValueError as exc:
        raise GateError(f"unknown enforcement level {name!r}; allowed: {list(_LADDER)}") from exc


def load_results(paths: list[Path]) -> list[dict[str, Any]]:
    documents = []
    for path in paths:
        if path.is_dir():
            documents.extend(
                json.loads(item.read_text(encoding="utf-8"))
                for item in sorted(path.glob("*.json"))
                if not item.name.endswith(".sweep.json")
            )
        elif path.is_file():
            documents.append(json.loads(path.read_text(encoding="utf-8")))
    return documents


def judge(documents: list[dict[str, Any]], contract: PerformanceContract) -> dict[str, Any]:
    """읽은 결과들을 판정한다.

    무엇을 확인했고 무엇을 확인하지 못했는지 함께 돌려준다. 확인하지 못한 것을
    통과로 세면 게이트가 아무것도 막지 않으면서 통과를 보고한다.
    """
    if not documents:
        raise GateError("no benchmark results to judge; 측정을 먼저 실행한다")

    regression_enforcement = str(load_yaml_mapping(REGRESSION_PATH).get("enforcement", "observe"))
    _level(regression_enforcement)
    checked: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for document in documents:
        run_id = document["run"]["id"]
        regression = document.get("regression")
        if regression is None:
            skipped.append({"run": run_id, "check": "regression",
                            "reason": "이 구성의 baseline이 없어 견줄 대상이 없다"})
        elif regression["status"] == "not_evaluated":
            skipped.append({"run": run_id, "check": "regression",
                            "reason": "baseline의 통계를 이 실행이 만들지 못했다"})
        else:
            entry = {"run": run_id, "check": "regression", "enforcement": regression_enforcement,
                     "status": regression["status"]}
            checked.append(entry)
            if regression["status"] == "regressed" and regression_enforcement in _BLOCKING:
                blocked.append({**entry, "detail": [
                    f"{c['metric']} {c['statistic']} {c['change_ratio']*100:+.1f}%"
                    for c in regression["comparisons"] if c["status"] == "regressed"
                ]})

        verdict = document.get("verdict")
        if not verdict:
            skipped.append({"run": run_id, "check": "slo", "reason": "판정이 기록되지 않았다"})
            continue
        enforcement = str(verdict["enforcement"])
        failures = [o for o in verdict.get("objectives") or [] if o["status"] == "fail"]
        evaluated = [o for o in verdict.get("objectives") or [] if o["status"] != "not_evaluated"]
        if not evaluated:
            skipped.append({"run": run_id, "check": "slo",
                            "reason": "임계값이 정해진 objective가 없다"})
            continue
        entry = {"run": run_id, "check": "slo", "enforcement": enforcement,
                 "status": "fail" if failures else "pass"}
        checked.append(entry)
        if failures and enforcement in _BLOCKING:
            blocked.append({**entry, "detail": [
                f"{o['metric']} {o['statistic']} {o['observed']} > {o['threshold']}" for o in failures
            ]})

    if not checked:
        # 아무것도 확인하지 못한 게이트는 게이트가 아니다. 통과로 세면 baseline을
        # 지우거나 임계값을 비우는 것만으로 게이트를 무력화할 수 있다.
        raise GateError(
            "no performance check could be performed; "
            + "; ".join(f"{s['check']}: {s['reason']}" for s in skipped[:3])
        )
    return {"status": "fail" if blocked else "pass", "checked": checked,
            "blocked": blocked, "skipped": skipped}
