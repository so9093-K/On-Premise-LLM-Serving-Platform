from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Iterable

from .gpu_budget import Participant, budget_snapshot, plan_activation


@dataclass(frozen=True)
class RuntimeTransitionPlan:
    target_key: str
    desired_state: str
    force: bool
    current_state: str
    no_op: bool
    admissible: bool
    requires_force: bool
    start: tuple[str, ...]
    stop: tuple[str, ...]
    prerequisites: tuple[str, ...]
    budget_before: dict[str, float]
    budget_after: dict[str, float]
    impact: tuple[dict[str, object], ...]
    reason: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "target_key": self.target_key,
            "desired_state": self.desired_state,
            "force": self.force,
            "current_state": self.current_state,
            "no_op": self.no_op,
            "admissible": self.admissible,
            "requires_force": self.requires_force,
            "start": list(self.start),
            "stop": list(self.stop),
            "prerequisites": list(self.prerequisites),
            "budget": {
                "before": self.budget_before,
                "after": self.budget_after,
            },
            "impact": [dict(item) for item in self.impact],
            "reason": self.reason,
        }

    @property
    def digest(self) -> str:
        raw = json.dumps(
            self.payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def response(self) -> dict[str, object]:
        return {**self.payload(), "plan_digest": self.digest}


def _budget_view(*, ceiling: float, used: float) -> dict[str, float]:
    return {
        "ceiling": round(ceiling, 4),
        "used": round(used, 4),
        "free": round(ceiling - used, 4),
    }


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def plan_runtime_transition(
    participants: list[Participant],
    target_key: str,
    desired_state: str,
    *,
    force: bool = False,
    prerequisites: Iterable[str] = (),
    ceiling: float,
) -> RuntimeTransitionPlan:
    """현재 runtime/GPU snapshot에서 side-effect 없이 transition 계획을 계산한다.

    GPU admission 결정은 ``gpu_budget.plan_activation``을 그대로 사용한다. 이 함수는
    runtime transition에 필요한 prerequisite 집합, stop/no-op 의미와 digest만 더한다.
    실제 실행 경로도 같은 입력으로 이 함수를 다시 호출해야 한다.
    """
    if desired_state not in {"active", "stopped"}:
        raise ValueError("desired_state must be 'active' or 'stopped'")

    by_key = {participant.key: participant for participant in participants}
    target = by_key.get(target_key)
    if target is None:
        raise ValueError(f"runtime participant not found: {target_key}")

    prerequisite_keys = _dedupe(prerequisites)
    missing = [key for key in prerequisite_keys if key not in by_key]
    if missing:
        raise ValueError(f"runtime prerequisite participant not found: {missing[0]}")

    snapshot = budget_snapshot(participants, ceiling=ceiling)
    before_used = float(snapshot["used"])
    current_state = "active" if target.active else "stopped"

    if desired_state == "stopped":
        # force는 activation eviction에만 의미가 있다. stop plan에서는 canonical
        # payload에서 false로 정규화해, 기존 호출자가 무의미한 force=true를 보내도
        # review digest와 apply-time 재계산이 같은 transition intent를 가리키게 한다.
        stop = (target_key,) if target.active else ()
        after_used = before_used - (target.vram_fraction if target.active else 0.0)
        impact = (
            ({"key": target_key, "action": "stop", "criticality": target.criticality},)
            if stop
            else ()
        )
        return RuntimeTransitionPlan(
            target_key=target_key,
            desired_state=desired_state,
            force=False,
            current_state=current_state,
            no_op=not stop,
            admissible=True,
            requires_force=False,
            start=(),
            stop=stop,
            prerequisites=prerequisite_keys,
            budget_before=_budget_view(ceiling=ceiling, used=before_used),
            budget_after=_budget_view(ceiling=ceiling, used=max(0.0, after_used)),
            impact=impact,
        )

    to_start = [key for key in prerequisite_keys if not by_key[key].active]
    if not target.active:
        to_start.append(target_key)
    start = _dedupe(to_start)
    if not start:
        return RuntimeTransitionPlan(
            target_key=target_key,
            desired_state=desired_state,
            force=force,
            current_state=current_state,
            no_op=True,
            admissible=True,
            requires_force=False,
            start=(),
            stop=(),
            prerequisites=prerequisite_keys,
            budget_before=_budget_view(ceiling=ceiling, used=before_used),
            budget_after=_budget_view(ceiling=ceiling, used=before_used),
            impact=(),
        )

    required = sum(by_key[key].vram_fraction for key in start)
    protected = set(start) | {target_key}
    admission_participants = [
        replace(participant, evictable=False)
        if participant.key in protected
        else participant
        for participant in participants
    ]
    admission = plan_activation(
        admission_participants,
        f"__runtime_transition__:{target_key}",
        required,
        ceiling=ceiling,
    )

    victims = tuple(admission.victims) if admission.feasible else ()
    victim_fraction = sum(by_key[key].vram_fraction for key in victims)
    projected_used = before_used - victim_fraction + required
    requires_force = bool(victims) and not force
    admissible = admission.feasible and not requires_force
    impact = tuple(
        {
            "key": key,
            "action": "stop",
            "criticality": by_key[key].criticality,
        }
        for key in victims
    )

    return RuntimeTransitionPlan(
        target_key=target_key,
        desired_state=desired_state,
        force=force,
        current_state=current_state,
        no_op=False,
        admissible=admissible,
        requires_force=requires_force,
        start=start,
        stop=victims,
        prerequisites=prerequisite_keys,
        budget_before=_budget_view(ceiling=ceiling, used=before_used),
        budget_after=_budget_view(ceiling=ceiling, used=projected_used),
        impact=impact,
        reason=admission.reason or None,
    )
