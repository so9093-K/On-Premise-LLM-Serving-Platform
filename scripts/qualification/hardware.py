from __future__ import annotations

import csv
import subprocess
from dataclasses import dataclass


class QualificationHardwareError(RuntimeError):
    """Qualification hardware fingerprint를 단일하게 관측할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class GpuObservation:
    name: str
    uuid: str
    memory_total_mib: int
    driver_version: str

    def as_context(self) -> dict[str, object]:
        return {
            "gpu": self.name,
            "gpu_uuid": self.uuid,
            "gpu_memory_total_mib": self.memory_total_mib,
            "driver_version": self.driver_version,
        }


def parse_nvidia_smi_output(output: str) -> GpuObservation:
    rows = [
        [column.strip() for column in row]
        for row in csv.reader(line for line in output.splitlines() if line.strip())
    ]
    if len(rows) != 1:
        raise QualificationHardwareError(
            "qualification producer v1 requires exactly one visible NVIDIA GPU; "
            f"observed {len(rows)}"
        )
    row = rows[0]
    if len(row) != 4 or not all(row):
        raise QualificationHardwareError(
            "nvidia-smi must return name, uuid, memory.total, and driver_version"
        )
    name, uuid, memory_total, driver_version = row
    try:
        memory_total_mib = int(memory_total)
    except ValueError as exc:
        raise QualificationHardwareError(
            f"nvidia-smi returned invalid memory.total {memory_total!r}"
        ) from exc
    return GpuObservation(
        name=name,
        uuid=uuid,
        memory_total_mib=memory_total_mib,
        driver_version=driver_version,
    )


def observe_nvidia_gpu(binary: str = "nvidia-smi") -> GpuObservation:
    try:
        completed = subprocess.run(
            [
                binary,
                "--query-gpu=name,uuid,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise QualificationHardwareError(f"failed to observe NVIDIA GPU: {exc}") from exc
    return parse_nvidia_smi_output(completed.stdout)
