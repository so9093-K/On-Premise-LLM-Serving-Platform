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
# Tolerance so float arithmetic on fractions (e.g. 0.85 - 0.785) doesn't spuriously
# demand an extra victim or reject an exact fit.
_EPS = 1e-9


@dataclass(frozen=True)
class Participant:
    key: str
    vram_fraction: float
    active: bool
    # Higher priority is evicted later. The main model is highest and is never
    # auto-evicted to make room for another model (evictable=False).
    priority: int = 0
    evictable: bool = True
    # Descriptive role (resource_control.criticality), surfaced in the budget view
    # so operators see what a candidate eviction would degrade. Not used by the
    # planner itself (which orders by priority/fraction).
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
    def needs_eviction(self) -> bool:
        return self.feasible and bool(self.victims)

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
    """Decide whether ``target_key`` (costing ``target_fraction``) can be active.

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
    """Operator-facing ledger view: per-participant cost/state + budget totals."""
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
