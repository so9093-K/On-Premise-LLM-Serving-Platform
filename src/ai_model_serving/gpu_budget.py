"""Shared GPU VRAM admission planning.

All vLLM runtimes on the host share one GPU. Each runtime reserves a fixed
fraction of total VRAM (its ``--gpu-memory-utilization``), so the GPU budget is
simply the sum of the *active* runtimes' fractions, which must stay under a
ceiling. This module is the single, pure decision point for "can model X be
loaded, and if not, what must be stopped first" -- it has no I/O so it is fully
unit-testable and is the authority both the main-model switch and the secondary
on/off paths consult.

Cost is the static reservation (``gpu_memory_utilization``), not a live
measurement; the ceiling carries headroom for fragmentation/overhead.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CEILING = 0.95
# fraction에 대한 float 연산(예: 0.85 - 0.785)이 오차로 인해 불필요한 victim을
# 추가로 요구하거나 정확히 맞는 경우를 거부하지 않도록 두는 허용 오차.
_EPS = 1e-9


@dataclass(frozen=True)
class Participant:
    key: str
    vram_fraction: float
    active: bool
    # priority가 높을수록 나중에 축출된다. main 모델이 가장 높으며 다른 모델을 위한
    # 공간 확보를 위해 자동 축출되지 않는다(evictable=False).
    priority: int = 0
    evictable: bool = True
    # 서술적 역할(resource_control.criticality). budget view에 노출되어 운영자가
    # 축출 후보가 무엇을 저하시킬지 알 수 있게 한다. planner 자체는(priority/fraction
    # 순으로 정렬하므로) 이 값을 사용하지 않는다.
    criticality: str | None = None


@dataclass(frozen=True)
class AdmissionResult:
    feasible: bool
    victims: tuple[str, ...] = ()
    required: float = 0.0
    used_by_others: float = 0.0
    ceiling: float = DEFAULT_CEILING
    reason: str = ""

    @property
    def already_fits(self) -> bool:
        return self.feasible and not self.victims

    @property
    def available(self) -> float:
        return self.ceiling - self.used_by_others


def plan_activation(
    participants: list[Participant],
    target_key: str,
    target_fraction: float,
    *,
    ceiling: float = DEFAULT_CEILING,
) -> AdmissionResult:
    """``target_key``가 ``target_fraction``만큼의 GPU를 사용해도 되는지 판단한다.

    The target's own current residency does not count against it (so reloading or
    replacing an already-active runtime is feasible in place). If it does not fit,
    evictable active participants are selected lowest-priority-first (largest
    fraction first within a priority, to minimise the number stopped) until enough
    room is freed. If even stopping every evictable participant is insufficient,
    the result is infeasible with no victims.
    """
    others = [p for p in participants if p.key != target_key]
    used_by_others = sum(p.vram_fraction for p in others if p.active)
    available = ceiling - used_by_others

    if target_fraction <= available + _EPS:
        return AdmissionResult(
            feasible=True,
            victims=(),
            required=target_fraction,
            used_by_others=used_by_others,
            ceiling=ceiling,
        )

    deficit = target_fraction - available
    candidates = sorted(
        (p for p in others if p.active and p.evictable),
        key=lambda p: (p.priority, -p.vram_fraction, p.key),
    )
    victims: list[str] = []
    freed = 0.0
    for candidate in candidates:
        if freed >= deficit - _EPS:
            break
        victims.append(candidate.key)
        freed += candidate.vram_fraction

    if freed < deficit - _EPS:
        return AdmissionResult(
            feasible=False,
            victims=(),
            required=target_fraction,
            used_by_others=used_by_others,
            ceiling=ceiling,
            reason=(
                f"{target_key} needs {target_fraction:.3f} but only "
                f"{available + freed:.3f} can be freed under ceiling {ceiling:.3f}"
            ),
        )

    return AdmissionResult(
        feasible=True,
        victims=tuple(victims),
        required=target_fraction,
        used_by_others=used_by_others,
        ceiling=ceiling,
    )


def budget_snapshot(
    participants: list[Participant], *, ceiling: float = DEFAULT_CEILING
) -> dict:
    """참여자별 비용·상태와 전체 예산을 포함한 운영자용 ledger를 반환한다."""
    used = sum(p.vram_fraction for p in participants if p.active)
    return {
        "ceiling": ceiling,
        "used": round(used, 4),
        "free": round(ceiling - used, 4),
        "participants": [
            {
                "key": p.key,
                "vram_fraction": p.vram_fraction,
                "active": p.active,
                "priority": p.priority,
                "evictable": p.evictable,
                "criticality": p.criticality,
            }
            for p in participants
        ],
    }
