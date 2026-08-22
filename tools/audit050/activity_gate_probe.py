"""AUDIT-050 deterministic activity-gating sketch.

This is an algorithm probe only. It does not decide the production threshold.
It demonstrates hysteresis over silence, tone, and low-level noise.
"""

from __future__ import annotations

import json
import math


def rms(samples: list[float]) -> float:
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def run() -> dict[str, object]:
    block_size = 480
    quiet_threshold = 0.003
    active_threshold = 0.01
    activate_after = 2
    deactivate_after = 10
    blocks: list[tuple[str, list[float]]] = []
    blocks += [("silence", [0.0] * block_size)] * 20
    blocks += [
        ("tone", [0.1 * math.sin(2 * math.pi * 440 * i / 48_000)
                  for i in range(block_size)])
    ] * 20
    blocks += [("noise_floor", [0.001] * block_size)] * 20
    blocks += [("silence", [0.0] * block_size)] * 20

    active = False
    loud_blocks = 0
    quiet_blocks = 0
    transitions: list[dict[str, object]] = []
    active_ranges: list[list[int]] = []
    range_start: int | None = None
    for index, (kind, samples) in enumerate(blocks):
        level = rms(samples)
        if level >= active_threshold:
            loud_blocks += 1
            quiet_blocks = 0
        elif level <= quiet_threshold:
            quiet_blocks += 1
            loud_blocks = 0
        if not active and loud_blocks >= activate_after:
            active = True
            range_start = index - activate_after + 1
            transitions.append({"block": index, "state": "active", "kind": kind})
        if active and quiet_blocks >= deactivate_after:
            active = False
            assert range_start is not None
            active_ranges.append([range_start, index - deactivate_after + 1])
            range_start = None
            transitions.append({"block": index, "state": "quiet", "kind": kind})
    return {
        "active_threshold": active_threshold,
        "quiet_threshold": quiet_threshold,
        "activate_after_blocks": activate_after,
        "deactivate_after_blocks": deactivate_after,
        "transitions": transitions,
        "active_ranges": active_ranges,
        "noise_floor_activated": any(
            transition["kind"] == "noise_floor"
            and transition["state"] == "active"
            for transition in transitions
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
