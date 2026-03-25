# Life Benchmark Result

This folder contains a saved `codopt` run for the sample Conway's Game of Life benchmark.

## How To View The Saved Result In The Web UI

Run:

```bash
uv run --with fastapi --with uvicorn python main.py ui --run-root example/life_result/run 
```

Then open `http://127.0.0.1:8780`.

The UI lets you inspect:

- the branch tree on the score-vs-time graph
- parsed agent logs
- raw `agent.jsonl` logs
- the net Git diff for each node commit

## What This Benchmark Is

The sample benchmark lives in [life](/Users/rohanadwankar/codex-optimize/example/life).

- [life.py](/Users/rohanadwankar/codex-optimize/example/life/life.py) is the code Codex tries to optimize
- [benchmark.py](/Users/rohanadwankar/codex-optimize/example/life/benchmark.py) runs deterministic Game of Life workloads and writes a numeric score to `metric.json`
- [tests.py](/Users/rohanadwankar/codex-optimize/example/life/tests.py) checks exact correctness
- [INFO.md](/Users/rohanadwankar/codex-optimize/example/life/INFO.md) is the background/context file given to the agent

The benchmark score is throughput. Higher is better. The checksum in the metric file ensures the benchmark is still producing the same simulation output.

## How This Benchmark Was Linked Into Codopt

The run used these pieces of configuration:

- `--edit example/life/life.py`
- `--metric example/life/metric.json`
- `--command 'python3 example/life/benchmark.py'`
- `--test 'python3 example/life/tests.py'`
- `--info example/life/INFO.md`
- `--docker-image codopt-life:latest`

That means:

1. Codex is pointed at [life.py](/Users/rohanadwankar/codex-optimize/example/life/life.py) as the main optimization target.
2. After each agent turn, `codopt` resets the metric file, runs [benchmark.py](/Users/rohanadwankar/codex-optimize/example/life/benchmark.py), and reads the score from `example/life/metric.json`.
3. Then it resets the metric file again and runs [tests.py](/Users/rohanadwankar/codex-optimize/example/life/tests.py).
4. If the tests pass, the host records a trusted commit for that branch and the score becomes eligible for survival.

This is the general integration pattern for another codebase:

- choose the file or directory the agent should optimize
- provide a benchmark command that writes one machine-readable metric file
- provide a correctness test command
- provide a short info file that explains the task and constraints
- make sure everything runs inside a Docker image with the required toolchain

The metric file does not have to look exactly like the Life example. The current parser supports:

- plain text containing one numeric value
- JSON containing one numeric field, default key `score`

If your metric lives under a different JSON key, use `--metric-key`.
If lower numbers are better, use `--lower-is-better`.

## What This Specific Run Did

The saved run is [run-a76l0x](/Users/rohanadwankar/codex-optimize/example/life_result/run/run_state.json).

- baseline score: `1370007.93919744`
- winning node: `r3_pr2_pr1_pbaseline_0_1_1`
- winning score: `23967167.83435935`
- final survivor branches:
  - `r3-pr2-pr1-pbaseline-0-1-1`
  - `r3-pr2-pr1-pbaseline-0-1-0`

The winner and final branch summary are in [summary.json](/Users/rohanadwankar/codex-optimize/example/life_result/run/summary.json).
If you would like to look into how the winning optimization worked, here is a [Gemini Conversation](https://gemini.google.com/share/e65ec535cde4) where I asked it whether the succesive diffs were good ideas.

## How To Reconstruct The Real Run Repo From The Bundle

The real repo which is typically produce as a result of running the algorithm was bundled so as to make it easy to handle in Github.

If you want to inspect the actual Git branches and commits locally, reconstruct the repo from [run.bundle](/Users/rohanadwankar/codex-optimize/example/life_result/run.bundle):

```bash
git clone example/life_result/run.bundle /tmp/life_result_repo
cd /tmp/life_result_repo
git branch
```

That gives you a normal Git repo with the saved run branches:

- `main`
- `r3-pr2-pr1-pbaseline-0-1-0`
- `r3-pr2-pr1-pbaseline-0-1-1`

You can then run normal Git commands there such as `git log`, `git diff`, or `git checkout` to see the contents of the agent's run.

## How To Cross-Apply This To Another Repo

If you already have a codebase you want to optimize, the Life example maps directly:

- your hot code path replaces [life.py](/Users/rohanadwankar/codex-optimize/example/life/life.py)
- your benchmark script replaces [benchmark.py](/Users/rohanadwankar/codex-optimize/example/life/benchmark.py)
- your correctness suite replaces [tests.py](/Users/rohanadwankar/codex-optimize/example/life/tests.py)
- your agent briefing replaces [INFO.md](/Users/rohanadwankar/codex-optimize/example/life/INFO.md)

The important contract is:

- benchmark command must write a stable metric file
- test command must reliably reject incorrect optimizations
- Docker image must contain everything needed to edit, benchmark, and test
- the metric file is an output artifact, not a trusted input

That is the minimum wiring needed for `codopt` to recursively branch, score, and compare optimization attempts.
