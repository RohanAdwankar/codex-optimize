# Game Of Life Optimization Task

You are optimizing a deterministic Conway's Game of Life implementation.

Goal:
- Increase the score written to `metric.json`.
- Preserve exact correctness for every generation under `tests.py`.

Constraints:
- `life.py` is the intended edit target.
- The benchmark covers dense and medium-density boards, so wins that only help sparse data will usually not be enough.
- The tests compare exact final board states, not approximate behavior.

Potential optimization directions:
- reduce repeated modulo work
- reduce temporary allocations
- reuse buffers
- localize hot-loop variables
- avoid redundant neighbor reads
