from __future__ import annotations

import json
import time
from pathlib import Path

from life import run_simulation


WORKLOADS = [
    (72, 72, 45, 11),
    (80, 80, 40, 19),
    (96, 96, 30, 27),
]


def main() -> None:
    output = Path(__file__).with_name("metric.json")
    started = time.perf_counter()
    checksum = 0
    total_cells = 0
    for width, height, steps, seed in WORKLOADS:
        checksum ^= run_simulation(width, height, steps, seed)
        total_cells += width * height * steps
    elapsed = time.perf_counter() - started
    score = total_cells / elapsed
    output.write_text(
        json.dumps(
            {
                "score": score,
                "elapsed_seconds": elapsed,
                "total_cells": total_cells,
                "checksum": checksum,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
