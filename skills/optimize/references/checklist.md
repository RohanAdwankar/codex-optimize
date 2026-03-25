# Optimization Checklist

Use this checklist when wiring a new repo into `codopt`.

## Host Checks

- confirm current directory is the target repo
- confirm `.git` exists
- confirm `git`, `docker`, `uv`, and `python3` exist
- confirm Docker is running
- confirm `~/.codex/auth.json` exists
- confirm `codopt --help` works
- inspect `git status --short`
- if the repo is dirty, treat the current state as baseline unless the user says otherwise
- do not create a temp repo just to capture local tracked edits; `codopt` snapshots the working tree by default

## Repo Wiring Checks

- identify the hot file or directory to optimize
- identify or create a deterministic benchmark command
- make sure the benchmark writes one metric file
- identify or create correctness tests
- add an info file explaining the optimization target and constraints
- prefer a small repo-local harness directory when benchmark/test scripts need sibling fixtures
- use external temporary command files only when the script is fully self-contained
- make benchmark and test command files start with `#!/bin/sh` and `set -eu`
- make command files assume the repo root as the current directory
- make command files self-contained; do not depend on extra `/tmp` helper files
- for compiled repos, build into `./.codopt-build/` or another hidden repo-local path
- do not write the benchmark executable to the repo root or `/tmp`
- for Haskell, prefer `-odir` and `-hidir` and remove the old benchmark binary before rebuilding
- prefer `--info-text` for the first pass unless a tracked info file already exists
- prefer the default auto-image path first
- only add a Dockerfile override if the auto-image path fails
- if you add a Dockerfile override, include `python3`, `git`, and `uv`
- remember that codopt cleans up the ephemeral images it builds itself, but manual exploratory Docker images are still your responsibility
- if validation says a host toolchain is missing in-container, stop retrying auto-image and add the Docker override immediately

## First Run Checks

- run `codopt validate` before the full tournament
- prefer `--command-file` and `--test-file` shell snippets over giant inline commands
- avoid reusing an old `--run-root`; prefer a fresh path or omit it
- use `--source-mode head` only if the user explicitly wants to ignore current local edits
- start with `--branch 2 --max-agents 4 --max-depth 3 --time 180`
- verify baseline evaluation works before trying to tune anything else
- verify the metric file parses cleanly
- verify the tests fail when behavior is broken
- verify the winner produces a real diff instead of just metric-file churn

## After The Run

- inspect `summary.json`
- inspect the winner diff
- apply the winner diff back into the original repo
- rerun the original repo's own tests
