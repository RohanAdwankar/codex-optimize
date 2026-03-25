# Optimization Checklist

Use this checklist when wiring a new repo into `codopt`.

## Host Checks

- confirm current directory is the target repo
- confirm `.git` exists
- confirm `git`, `docker`, `uv`, and `python3` exist
- confirm Docker is running
- confirm `~/.codex/auth.json` exists

## Repo Wiring Checks

- identify the hot file or directory to optimize
- identify or create a deterministic benchmark command
- make sure the benchmark writes one metric file
- identify or create correctness tests
- add an info file explaining the optimization target and constraints
- add a Dockerfile that includes `python3`, `git`, and `uv`

## First Run Checks

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
