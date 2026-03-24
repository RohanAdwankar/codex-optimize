from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .orchestrator import run_codopt


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--model", default="gpt-5.4", help="Codex model")
    parser.add_argument("--run-root", help="Run root directory, default /tmp/codopt/<run-id>")
    parser.add_argument("--run-id", help="Optional explicit run identifier")
    parser.add_argument("--ui-port", type=int, default=8765, help="Local web UI port")
    parser.add_argument("--keep-worktrees", action="store_true", help="Retain worktree directories after the run")
    parser.add_argument("--allow-path", action="append", default=[], help="Extra allowed writable path")
    parser.add_argument("--no-open-ui", action="store_true", help="Do not attempt to open the browser automatically")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(run_codopt(args, Path(__file__).resolve().parents[1]))
