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
- check `git status --short`

If any of those are missing, stop and say exactly what is missing.

If the repo is dirty, do not ignore that. Treat the current working tree as the baseline the user wants optimized unless they explicitly say otherwise. `codopt` snapshots the working tree by default, so do not create a temp repo or manual baseline commit unless the user explicitly wants committed `HEAD` instead.

## Install And Prepare Codex-Optimize

Run `codopt` as an installed CLI.

The expected interface is:

```bash
codopt --help
```

If `codopt` is not installed yet, install it first:

```bash
uv tool install /path/to/codex-optimize
```

Or for a local non-installed smoke test:

```bash
uvx --from /path/to/codex-optimize codopt --help
```

If the user explicitly wants committed `HEAD` only instead of the current working tree, add `--source-mode head`.

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

In most cases, do not start by writing a Dockerfile. `codopt` can auto-generate and build a runtime image by default, including common compiled repos. But if validation says the benchmark or test command is missing a toolchain like `ghc`, `cargo`, `go`, `node`, or some system library in-container, stop trying the auto-image path and write the Docker override immediately.

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
4. Prefer `--info-text` for the first pass unless the repo already has a tracked, stable info file.
5. Prefer `--command-file` and `--test-file` immediately when the benchmark/test commands are more than a one-liner.
6. Prefer a small repo-local harness directory when the benchmark or test needs sibling fixtures. Prefer external temporary command files only when the script is fully self-contained.
7. Start those command files with `#!/bin/sh` and `set -eu`.
8. Treat the current working directory inside those command files as the repo root. Do not `cd /workspace`.
9. Keep the command files self-contained. Do not make them depend on extra `/tmp/*.py` helper files unless those helpers are inlined into the same shell script.
10. For compiled repos, build into a hidden repo-local directory like `./.codopt-build/` and put the executable there too.
11. Do not build into a top-level binary like `./runhs` or into `/tmp`; stale host-built binaries can make a broken container benchmark look healthy.
12. For Haskell, prefer `ghc --make ... -odir ./.codopt-build/obj -hidir ./.codopt-build/hi -o ./.codopt-build/<name>` and delete the old binary before rebuilding.
13. Do not hardcode a reused `--run-root`. Omit it or give each run a fresh unique path.
14. Run `codopt validate` first, without `--docker-image` or `--dockerfile`.
15. If validation fails because the container lacks the required toolchain or system packages, write the Docker override immediately and rerun validation.
16. Do not create a temp Git clone just to capture local tracked edits; `codopt` already snapshots the working tree by default.
17. Once validation succeeds, run `codopt run` with the same benchmark/test wiring and conservative defaults first.
18. Inspect the winner.
19. Apply the winner's diff back to the current repo.

## Conservative Default Run

Prefer these defaults unless the user asked for something else:

- `--branch 2`
- `--max-agents 4`
- `--max-depth 3`
- `--time 180`

Use the local UI unless the user asked for headless execution.

Example validation command:

```bash
codopt validate \
  --edit <target-file-or-dir> \
  --metric <metric-file> \
  --metric-key <json-key-if-needed> \
  --command-file <benchmark-command-file> \
  --branch 2 \
  --time 180 \
  --info-text "<short-optimization-brief>" \
  --max-agents 4 \
  --test-file <test-command-file> \
  --max-depth 3
```

If the repo is dirty and those edits are intentional, say that explicitly in the info text so the optimizer treats the current state as the baseline rather than some older clean commit.

Example full run:

```bash
codopt run \
  --edit <target-file-or-dir> \
  --metric <metric-file> \
  --metric-key <json-key-if-needed> \
  --command-file <benchmark-command-file> \
  --branch 2 \
  --time 180 \
  --info-text "<short-optimization-brief>" \
  --max-agents 4 \
  --test-file <test-command-file> \
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
- a compiler or runtime like `ghc` that is available on the host but missing in the generated container

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
codopt ui --run-root <run-root>
```

Use the UI to inspect:

- score-vs-time branch graph
- parsed logs
- raw logs
- net Git diff per node

## Install The Skill Correctly

Install the skill under one of these, depending on how your Codex is configured:

```bash
~/.codex/skills/optimize
```

```bash
.codex/skills/optimize
```

Then restart Codex.

## What To Say Back To The User

When you use this skill, report:

- what files or commands you added to wire the repo into `codopt`
- the exact `codopt` command you ran
- where the run root is
- whether the repo started dirty and whether you treated that state as the optimization baseline
- which node/branch won
- whether you applied the winning diff back to the original repo

## Reference Files

Read [references/checklist.md](references/checklist.md) before the first run.
Read [references/life-pattern.md](references/life-pattern.md) when you need a concrete example of how a repo is wired into `codopt`.
