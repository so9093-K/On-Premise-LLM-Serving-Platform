from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RiskDetector(Protocol):
    """Local risk detector protocol.

    Implementors return a complete assessment_response dict directly, without
    going through a vLLM runtime. The response must satisfy the same
    signal-only contract as vLLM-based detectors.
    """

    async def assess(self, text: str) -> dict[str, Any]: ...
