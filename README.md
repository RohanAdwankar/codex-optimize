# codex-optimize

Optimize any software with Codex. 

`codopt` clones your repository into a run directory, fans out candidate branches with git worktrees, runs one Codex agent per branch in its own Docker container, and evaluates each branch with a benchmark command plus a correctness test command. Surviving branches fork again in later rounds.

## Why?


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

## Example 

[`example/life`](./example/life) contains a Conway's Game of Life challenge chosen to be optimizable but not one-shottable.

- `life.py`: naive implementation
- `benchmark.py`: writes `metric.json`
- `tests.py`: exact correctness checks
- `INFO.md`: instructions for the agent
- `Dockerfile`: sample runtime image

View the result of my run in the UI :
```bash
uv run --with fastapi --with uvicorn python main.py ui --run-root example/life_result/run
```

Alternatively you can run it yourself.
First build the sample image:
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
