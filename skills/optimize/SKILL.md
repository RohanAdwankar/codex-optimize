---
name: optimize
description: Use when the user wants Codex to set up and run codex-optimize against the current repository, including wiring a benchmark, correctness tests, Docker image, and then extracting the winning optimization back into the working repo.
---

# Optimize

Use this skill when the user wants the current repository optimized with `codex-optimize`.

## Goal

Set up the current repo so `codex-optimize` can run, execute a bounded optimization tournament, and then bring the winning code changes back into the current repo.

## Preconditions

Before doing anything substantial, verify:

- current directory is the repo the user wants optimized
- `git`, `docker`, `uv`, and `python3` are available on the host
- Docker is running
- the host already has Codex auth in `~/.codex`

If any of those are missing, stop and say exactly what is missing.

## Clone And Prepare Codex-Optimize

If `codex-optimize` is not already available locally, clone:

```bash
git clone https://github.com/RohanAdwankar/codex-optimize /tmp/codex-optimize
```

Work from that clone when invoking the tool.

Do not assume the target repo already contains the integration files. Inspect it and add only the minimum needed wiring.

## What The Target Repo Needs

`codopt` needs four pieces:

1. An optimization target:
   A file or directory passed with `--edit`.

2. A benchmark command:
   This must write one metric file.

3. A correctness test command:
   This must fail when an optimization breaks behavior.

4. A short info file:
   This explains the objective and key constraints to the agent.

In most cases, do not start by writing a Dockerfile. `codopt` can auto-generate and build a runtime image by default.

## Metric File Contract

The metric file does not need to match the Life example exactly.

Supported forms:

- plain text containing one numeric value
- JSON containing one numeric field

Defaults:

- JSON key defaults to `score`
- higher values are treated as better

If the repo's natural metric is a different JSON key, pass `--metric-key`.
If lower values are better, pass `--lower-is-better`.

## Integration Workflow

1. Inspect the target repo and find the code path to optimize.
2. Add or identify a benchmark entrypoint that produces a stable metric file.
3. Add or identify correctness tests.
4. Add an info file for the optimization task.
5. Try the default `codopt` path without `--docker-image` or `--dockerfile`.
6. Only add a Dockerfile override if the default image path fails.
7. Run `codopt` with conservative defaults first.
8. Inspect the winner.
9. Apply the winner's diff back to the current repo.

## Conservative Default Run

Prefer these defaults unless the user asked for something else:

- `--branch 2`
- `--max-agents 4`
- `--max-depth 3`
- `--time 180`

Use the local UI unless the user asked for headless execution.

Example invocation from the `codex-optimize` clone:

```bash
uv run --with fastapi --with uvicorn python main.py \
  --edit <target-file-or-dir> \
  --metric <metric-file> \
  --metric-key <json-key-if-needed> \
  --command "<benchmark-command>" \
  --branch 2 \
  --time 180 \
  --info <info-file> \
  --max-agents 4 \
  --test "<test-command>" \
  --max-depth 3
```

If the metric is lower-is-better, add:

```bash
--lower-is-better
```

Only add one of these overrides when needed:

```bash
--docker-image <prebuilt-image>
```

```bash
--dockerfile <path-to-dockerfile>
```

Do not waste time on a huge search before proving the setup works.

## When A Docker Override Is Actually Needed

The default auto-image path is usually enough if the benchmark and test commands are ordinary repo-local commands.

Add a Docker override only when the default image build or container run fails because the repo depends on undeclared environment details such as:

- system packages or native libraries the auto image does not include
- unusual build steps that are not inferable from the repo files
- private or company-specific base images
- toolchains that need a very specific version or installation method
- services, drivers, or runtime components that need explicit setup

This is not about programming language. It is about environment specificity.

If you add a Docker override, the resulting image must include:

- `python3`
- `git`
- `uv`

And it should also include whatever the benchmark and test commands need in order to run successfully inside the container.

## Getting The Optimized Code Back

`codopt` runs against a cloned tournament repo in its run directory. It does not directly edit the original working repo.

After the run:

1. Read `summary.json` and identify the winner branch or winner node.
2. Get the winning commit diff from the run UI or the run repo.
3. Apply that diff back into the current repo.
4. Run the target repo's own tests locally in the original repo.

Prefer applying only the winner diff, not replacing the whole repo with the run clone.

## UI

For a live run, the UI starts automatically.

For a finished run:

```bash
uv run --with fastapi --with uvicorn python main.py ui --run-root <run-root>
```

Use the UI to inspect:

- score-vs-time branch graph
- parsed logs
- raw logs
- net Git diff per node

## What To Say Back To The User

When you use this skill, report:

- what files or commands you added to wire the repo into `codopt`
- the exact `codopt` command you ran
- where the run root is
- which node/branch won
- whether you applied the winning diff back to the original repo

## Reference Files

Read [references/checklist.md](references/checklist.md) before the first run.
Read [references/life-pattern.md](references/life-pattern.md) when you need a concrete example of how a repo is wired into `codopt`.
