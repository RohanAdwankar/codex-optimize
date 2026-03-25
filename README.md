# codex-optimize

https://github.com/user-attachments/assets/7646dab7-d12a-4574-a493-9d130e9042e9

Optimize any software with Codex. 

`codopt` clones your repository into a run directory, fans out candidate branches with git worktrees, runs one Codex agent per branch in its own Docker container, and evaluates each branch with a benchmark command plus a correctness test command. Surviving branches fork again in later rounds.

## Why?

One appraoch to AI assisted software optimization is to just point it to some code and then tell it to optimize it.
There are several problems with this:
1. Agents tend to cheat benchmarks, even unintentionally. One of the common behavior patterns when you tell an agent to maximize a value unconstrained is the agent will simply hack through the benchmarks and tests so produce a result that seems great but in closer inspection is not a substantive optimization.
2. Agents are non deterministic, so it can fail at the optimization one time and then the next time succeed even with the same prompt.
3. Agents can get lazy! This is very unintuitive but many times since it thinks that it has provided the answer, prompting "optimize" results in it concluding it is done. After it states that it is done, then since it being done is in its context it will just continue to believe this. In a sense, it has poisoned its own context.

codex-optimize attempt to sovle these problems:
1. codopt explicitly partions the source code, optimization tests, and correctness tests. since these parts are partioned and in git they can be reset to evaluate whether the source code changes were substantive while preventing the benchmark hacking behavior.
2. By running a beam search strategy, we can see a diverse variety of attempts and keep exploring the ones that work. The below example run shows a good example of this where some of the Codex agents actually degraded the quality of the optimization but the top candidates signficantly optimized the code.
3. By pruning nodes that are failing or stagnating, we can avoid context poisoning and get results over more iterations. This is also demonstrated in the example below were after some iterations some fail while some keep improving.

The core idea is to use the Codex SDK to optimize more deterministically than using Skills or prompting.

## Quick Start 

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

Read more about it in the result [README.MD](example/life_result/README.md).

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
