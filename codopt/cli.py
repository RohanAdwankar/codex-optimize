from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .orchestrator import run_codopt, validate_codopt
from .ui import serve_ui_forever


def write_scaffold(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "benchmark.sh": """#!/bin/sh
set -eu

build_dir="./.codopt-build"
bin_file="./.codopt-bench"
metric_file="metric.json"

mkdir -p "$build_dir"

# Replace this build command with the one for your repo.
# Keep build artifacts inside the repo clone, not /tmp.
echo "TODO: build benchmark target" >&2
exit 1
""",
        "test.sh": """#!/bin/sh
set -eu

build_dir="./.codopt-build"
bin_file="./.codopt-test"

mkdir -p "$build_dir"

# Replace this with a deterministic correctness check.
echo "TODO: run correctness test" >&2
exit 1
""",
        "Dockerfile": """FROM python:3.12-bookworm

RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        ca-certificates \\
        git \\
        python3 \\
        python3-pip \\
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /workspace
""",
        "INFO.md": """Optimize the target code for the chosen metric.

Constraints:
- Preserve externally visible behavior.
- Do not change the benchmark or correctness harness.
- Prefer incremental, correctness-preserving improvements.
""",
    }
    for name, content in files.items():
        path = output_dir / name
        path.write_text(content, encoding="utf-8")
        if name.endswith(".sh"):
            path.chmod(0o755)


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--edit", action="append", required=True, help="File or directory the agent may edit")
    parser.add_argument("--metric", required=True, help="Metric file written by the benchmark command")
    parser.add_argument("--metric-key", default="score", help="JSON key to read from the metric file when the metric file is JSON")
    parser.add_argument("--lower-is-better", action="store_true", help="Treat lower metric values as better; codopt will invert them for ranking")
    parser.add_argument("--command", help="Benchmark command that produces the metric file")
    parser.add_argument("--command-file", help="Path to a shell snippet file used as the benchmark command")
    parser.add_argument("--branch", type=int, required=True, help="Children per surviving node")
    parser.add_argument("--time", type=int, required=True, help="Per-node agent time budget in seconds")
    parser.add_argument("--info", "--info-file", dest="info", help="Background info file for the agent; may be outside the repo")
    parser.add_argument("--info-text", help="Inline background context for the agent")
    parser.add_argument("--max-agents", type=int, required=True, help="Cap on active nodes per round")
    parser.add_argument("--test", help="Correctness test command")
    parser.add_argument("--test-file", help="Path to a shell snippet file used as the correctness test command")
    parser.add_argument("--docker-image", help="Container image used for agent and eval runs")
    parser.add_argument("--dockerfile", help="Dockerfile to build and use for agent and eval runs")
    parser.add_argument("--source-mode", choices=["working-tree", "head"], default="working-tree", help="Source baseline to optimize: current working tree or git HEAD")
    parser.add_argument("--rounds", type=int, default=2, help="How many tournament rounds to run")
    parser.add_argument("--max-depth", type=int, help="Alias for --rounds; stop recursion after this depth")
    parser.add_argument("--model", default="gpt-5.4", help="Codex model")
    parser.add_argument("--run-root", help="Run root directory, default /tmp/codopt/<run-id>")
    parser.add_argument("--run-id", help="Optional explicit run identifier")
    parser.add_argument("--ui-port", type=int, default=8765, help="Local web UI port")
    parser.add_argument("--keep-worktrees", action="store_true", help="Retain worktree directories after the run")
    parser.add_argument("--allow-path", action="append", default=[], help="Extra allowed writable path")
    parser.add_argument("--no-open-ui", action="store_true", help="Do not attempt to open the browser automatically")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize a metric with Codex + git worktrees + Docker")
    subparsers = parser.add_subparsers(dest="subcommand")

    run_parser = subparsers.add_parser("run", help="Run a full optimization tournament")
    add_run_arguments(run_parser)

    validate_parser = subparsers.add_parser("validate", help="Validate codopt wiring without running a full tournament")
    add_run_arguments(validate_parser)

    ui_parser = subparsers.add_parser("ui", help="Inspect a codopt run in the local UI")
    ui_parser.add_argument("--run-root", help="Run directory containing run_state.json")
    ui_parser.add_argument("--state-file", help="Explicit path to run_state.json")
    ui_parser.add_argument("--ui-port", type=int, default=8765, help="Local web UI port")
    ui_parser.add_argument("--no-open-ui", action="store_true", help="Do not attempt to open the browser automatically")
    ui_parser.add_argument(
        "--allow-prune",
        action="store_true",
        help="Enable prune requests by wiring the run control directory instead of read-only viewer mode",
    )

    scaffold_parser = subparsers.add_parser("scaffold", help="Write starter benchmark/test/Docker scaffolding")
    scaffold_parser.add_argument("--output-dir", default="codopt_scaffold", help="Directory to write starter files into")
    return parser


def _normalize_run_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> argparse.Namespace:
    if args.max_depth is not None:
        if args.max_depth < 1:
            parser.error("--max-depth must be at least 1")
        if args.rounds != 2 and args.rounds != args.max_depth:
            parser.error("--rounds and --max-depth must match when both are provided")
        args.rounds = args.max_depth
    if bool(args.command) == bool(args.command_file):
        parser.error("pass exactly one of --command or --command-file")
    if bool(args.test) == bool(args.test_file):
        parser.error("pass exactly one of --test or --test-file")
    if not args.info and not args.info_text:
        parser.error("pass one of --info or --info-text")
    return args


def _resolve_view_paths(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[Path, Path | None, bool]:
    if not args.state_file and not args.run_root:
        parser.error("one of --run-root or --state-file is required")
    if args.state_file:
        state_file = Path(args.state_file).expanduser().resolve()
        run_root = state_file.parent
    else:
        run_root = Path(args.run_root).expanduser().resolve()
        state_file = run_root / "run_state.json"
    if not state_file.exists():
        parser.error(f"state file not found: {state_file}")
    control_dir = run_root / "control" if args.allow_prune else None
    return state_file, control_dir, not args.allow_prune


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = build_parser()
    argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        raise SystemExit(2)
    if argv[0] not in {"run", "validate", "ui", "scaffold", "-h", "--help"}:
        argv = ["run", *argv]
    args = parser.parse_args(argv)

    if args.subcommand == "ui":
        state_file, control_dir, read_only = _resolve_view_paths(parser, args)
        serve_ui_forever(
            state_file,
            control_dir,
            args.ui_port,
            open_browser=not args.no_open_ui,
            read_only=read_only,
        )
        return

    if args.subcommand == "scaffold":
        output_dir = Path(args.output_dir).expanduser().resolve()
        write_scaffold(output_dir)
        print(f"Wrote starter codopt files to {output_dir}")
        return

    if args.subcommand == "validate":
        args = _normalize_run_args(parser, args)
        asyncio.run(validate_codopt(args, project_root))
        return

    if args.subcommand == "run":
        args = _normalize_run_args(parser, args)
        asyncio.run(run_codopt(args, project_root))
        return

    parser.print_help()
    raise SystemExit(2)
