# codex-optimize

Optimize a numeric software metric with Codex, git worktrees, and Docker.

`codopt` clones the current repository into a run directory, fans out candidate branches with git worktrees, runs one Codex agent per branch in its own Docker container, and evaluates each branch with a benchmark command plus a correctness test command. Surviving branches fork again in later rounds.

## Status

This repository now contains a working first pass of the core system:

- Python CLI orchestrator
- per-agent Docker worker containers
- git worktree tournament branching
- benchmark + test evaluation in separate containers
- metric parsing from plain numeric files or JSON files with a top-level `score`
- run artifacts and a local web UI with node logs and manual prune requests
- a sample optimization example in [`example/life`](./example/life)

## CLI

```bash
python main.py \
  --edit example/life/life.py \
  --metric example/life/metric.json \
  --command "python3 example/life/benchmark.py" \
  --branch 3 \
  --time 120 \
  --info example/life/INFO.md \
  --max-agents 6 \
  --test "python3 example/life/tests.py" \
  --docker-image codopt-life:latest \
  --rounds 2
```

Flags:

- `--edit`: repeatable file or directory the agent may edit
- `--metric`: metric file written by the benchmark command
- `--command`: benchmark command
- `--branch`: children per surviving node
- `--time`: per-node Codex time budget in seconds
- `--info`: background context given to the agent
- `--max-agents`: active-node cap used to decide survivor count
- `--test`: correctness test command
- `--docker-image`: required container image for agent and evaluation runs
- `--rounds`: tournament depth
- `--allow-path`: repeatable extra writable path
- `--keep-worktrees`: keep worktree directories after completion

## Docker contract

The Docker image used with `codopt` must contain:

- `python3`
- `git`
- `uv`

Agent containers need network access so the Codex runtime can start and reach the API. Evaluation containers are run with `--network none`.

`codopt` seeds a run-scoped `CODEX_HOME` from the host `~/.codex` directory and mounts it into every agent container.

## Sample benchmark

[`example/life`](./example/life) contains a deterministic Conway's Game of Life workload chosen to be optimizable but not trivially one-shottable.

- `life.py`: intentionally naive implementation
- `benchmark.py`: writes `metric.json`
- `tests.py`: exact correctness checks
- `INFO.md`: instructions for the agent
- `Dockerfile`: sample runtime image

Build the sample image:

```bash
docker build -t codopt-life:latest example/life
```

Then run:

```bash
python main.py \
  --edit example/life/life.py \
  --metric example/life/metric.json \
  --command "python3 example/life/benchmark.py" \
  --branch 3 \
  --time 120 \
  --info example/life/INFO.md \
  --max-agents 6 \
  --test "python3 example/life/tests.py" \
  --docker-image codopt-life:latest \
  --rounds 2
```

## Artifacts

Each run writes to `/tmp/codopt/<run-id>` by default:

- cloned repo used for the tournament
- per-node prompt, result, and agent event logs
- `run_state.json`
- `events.jsonl`
- `summary.json`

The UI is served on `http://127.0.0.1:<port>` and shows current node state, scores, metric snapshots, and agent logs.
