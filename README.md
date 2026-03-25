# codex-optimize

https://github.com/user-attachments/assets/7646dab7-d12a-4574-a493-9d130e9042e9

Optimize any software with the Codex SDK. 

`codopt` clones your repository into a run directory, fans out candidate branches with git worktrees, runs one Codex agent per branch in its own Docker container, and evaluates each branch with a benchmark command plus a correctness test command. Surviving branches fork again in later rounds.

By default, `codopt` snapshots your current working tree into a disposable internal repo first, so local tracked edits are part of the optimization baseline even if they are not committed yet.

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
- `Dockerfile`: optional sample runtime image override

Install the CLI locally for testing:

```bash
uv tool install /path/to/codex-optimize
```

Or run it directly from a local checkout without publishing:

```bash
uvx --from /path/to/codex-optimize codopt --help
```

View the result of my run in the UI :
```bash
codopt ui --run-root example/life_result/run
```

Alternatively you can run it yourself.

Run:
```bash
codopt run \
  --edit example/life/life.py \
  --metric example/life/metric.json \
  --metric-key score \
  --command "python3 example/life/benchmark.py" \
  --branch 3 \
  --time 120 \
  --info example/life/INFO.md \
  --max-agents 6 \
  --test "python3 example/life/tests.py" \
  --rounds 2
```

Read more about this run in the result's [README.MD](example/life_result/README.md).

An alternative option to running the program yourself is asking your agent to use it! 
If this is your goal there is an [optimize](skills/optimize/SKILL.md) skill folder you can copy into `~/.codex/skills/optimize` and restart Codex.
 
## CLI Flags

- `--edit`: repeatable file or directory the agent may edit
- `--metric`: metric file written by the benchmark command
- `--metric-key`: JSON key to read when the metric file is JSON, default `score`
- `--lower-is-better`: invert the parsed metric value for ranking
- `--command`: benchmark command
- `--command-file`: path to a shell snippet file executed with `sh -eu`; repo-local files run from the cloned repo path, external files are copied into the run root
- `--branch`: children per surviving node
- `--time`: per-node Codex time budget in seconds
- `--info` / `--info-file`: background context file given to the agent, may be outside the repo
- `--info-text`: inline background context for the agent
- `--max-agents`: active-node cap used to decide survivor count
- `--test`: correctness test command
- `--test-file`: path to a shell snippet file executed with `sh -eu`; repo-local files run from the cloned repo path, external files are copied into the run root
- `--docker-image`: optional prebuilt container image for agent and evaluation runs
- `--dockerfile`: optional Dockerfile to build and use for agent and evaluation runs
- `--source-mode`: `working-tree` (default) snapshots the current repo state; `head` uses Git `HEAD` only
- `--rounds`: tournament depth
- `--allow-path`: repeatable extra writable path
- `--keep-worktrees`: keep worktree directories after completion

There is also a validation mode:

```bash
codopt validate ...
```

Use it before the first full run. It checks host prerequisites, benchmark/test success on the host clone, benchmark/test success inside Docker, and metric parsing.

### Metric Key 

Your benchmark command does not need to match the Life example , but it does need to produce one metric file that `codopt` can parse:
- if the metric file is plain text, it must contain a single numeric value
- if the metric file is JSON, `codopt` reads one numeric field from it
- by default that JSON field is `score` unless a metric-key flag is passed
- by default higher values are treated as better unless the lower-is-better flag is passed

### Requirements

Before running `codopt`, you need:

- `git`
- `docker`
- `uv`
- Python 3 on the host
- an existing Codex login on the host in `~/.codex`

Important setup notes:

- run `codopt` from the root of the Git repo you want to optimize
- Docker must be running
- `codopt` seeds a run-local `CODEX_HOME` from your host `~/.codex`, so you need to already be authenticated before starting
- by default `codopt` auto-generates and builds a runtime image for the repo, with special handling for common project types like Python, Node, Rust, Go, Java, and Haskell
- if you override with `--docker-image` or `--dockerfile`, the resulting image must contain `python3`, `git`, and `uv`
- `codopt` removes the ephemeral images it builds itself after `validate` and `run`, so repeated runs do not keep piling up `codopt-auto-*` images

### First-Run Pattern

For a new repo, prefer this sequence:

1. Wire a benchmark command, test command, and info text or info file.
2. Run `codopt validate ...`.
3. If validation fails in the auto-generated image, only then add `--dockerfile` or `--docker-image`.
4. Once validation succeeds, run the full bounded tournament with `codopt run ...`.

If you explicitly want to ignore local edits and optimize committed `HEAD` only, add `--source-mode head`.

If you do not want to add helper files to the repo yet, use:

- `--command-file`
- `--test-file`
- `--info-text`

Those are the easiest way to do a first exploratory run without committing setup files into the target repository. Repo-local command files run in the cloned repo directly, so sibling fixtures in the same harness directory can come along with the snapshot baseline. External command files are copied into the run root and executed with `sh -eu`.

Command-file rules:

- assume the current working directory is the cloned repo root on both host validation and container validation
- do not hardcode `cd /workspace`
- keep the command file self-contained, or only reference files that are already tracked in the repo
- do not make the command file depend on extra `/tmp/*.py` helper files unless you inline them into the script or commit them into the repo first
- for compiled repos, build into a hidden repo-local path like `./.codopt-build/` and write the executable there too
- do not rely on a top-level binary like `./runhs` or `/tmp/mybench`; stale host-built binaries can mask container failures
- for Haskell specifically, prefer `-odir ./.codopt-build/obj -hidir ./.codopt-build/hi` and remove any old benchmark binary before rebuilding

Starter scaffolding:

```bash
codopt scaffold --output-dir codopt_scaffold
```

This writes starter `benchmark.sh`, `test.sh`, `Dockerfile`, and `INFO.md` files you can adapt for a new repo.
