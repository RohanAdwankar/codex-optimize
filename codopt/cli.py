from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .orchestrator import run_codopt
from .ui import serve_ui_forever


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize a metric with Codex + git worktrees + Docker")
    parser.add_argument("--edit", action="append", required=True, help="File or directory the agent may edit")
    parser.add_argument("--metric", required=True, help="Metric file written by the benchmark command")
    parser.add_argument("--command", required=True, help="Benchmark command that produces the metric file")
    parser.add_argument("--branch", type=int, required=True, help="Children per surviving node")
    parser.add_argument("--time", type=int, required=True, help="Per-node agent time budget in seconds")
    parser.add_argument("--info", required=True, help="Background info file for the agent")
    parser.add_argument("--max-agents", type=int, required=True, help="Cap on active nodes per round")
    parser.add_argument("--test", required=True, help="Correctness test command")
    parser.add_argument("--docker-image", required=True, help="Container image used for agent and eval runs")
    parser.add_argument("--rounds", type=int, default=2, help="How many tournament rounds to run")
    parser.add_argument("--max-depth", type=int, help="Alias for --rounds; stop recursion after this depth")
    parser.add_argument("--model", default="gpt-5.4", help="Codex model")
    parser.add_argument("--run-root", help="Run root directory, default /tmp/codopt/<run-id>")
    parser.add_argument("--run-id", help="Optional explicit run identifier")
    parser.add_argument("--ui-port", type=int, default=8765, help="Local web UI port")
    parser.add_argument("--keep-worktrees", action="store_true", help="Retain worktree directories after the run")
    parser.add_argument("--allow-path", action="append", default=[], help="Extra allowed writable path")
    parser.add_argument("--no-open-ui", action="store_true", help="Do not attempt to open the browser automatically")
    return parser


def build_view_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a codopt run in the local UI")
    parser.add_argument("--run-root", help="Run directory containing run_state.json")
    parser.add_argument("--state-file", help="Explicit path to run_state.json")
    parser.add_argument("--ui-port", type=int, default=8765, help="Local web UI port")
    parser.add_argument("--no-open-ui", action="store_true", help="Do not attempt to open the browser automatically")
    parser.add_argument(
        "--allow-prune",
        action="store_true",
        help="Enable prune requests by wiring the run control directory instead of read-only viewer mode",
    )
    return parser


def _normalize_run_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> argparse.Namespace:
    if args.max_depth is not None:
        if args.max_depth < 1:
            parser.error("--max-depth must be at least 1")
        if args.rounds != 2 and args.rounds != args.max_depth:
            parser.error("--rounds and --max-depth must match when both are provided")
        args.rounds = args.max_depth
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
    if len(sys.argv) > 1 and sys.argv[1] == "ui":
        parser = build_view_parser()
        args = parser.parse_args(sys.argv[2:])
        state_file, control_dir, read_only = _resolve_view_paths(parser, args)
        serve_ui_forever(
            state_file,
            control_dir,
            args.ui_port,
            open_browser=not args.no_open_ui,
            read_only=read_only,
        )
        return

    parser = build_run_parser()
    args = _normalize_run_args(parser, parser.parse_args())
    asyncio.run(run_codopt(args, project_root))
