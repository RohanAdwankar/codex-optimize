from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any


def ensure_sdk_paths(project_root: Path) -> None:
    sdk_python_dir = project_root / "docs" / "sdk" / "python"
    sdk_src_dir = sdk_python_dir / "src"
    sdk_root = str(sdk_python_dir)
    sdk_src = str(sdk_src_dir)
    for candidate in (sdk_root, sdk_src):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="codopt worker")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--mode", choices=["start", "fork"], required=True)
    parser.add_argument("--parent-thread-id")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--time-limit", type=int, required=True)
    return parser.parse_args()


def json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Cannot serialize {type(value)!r}")


async def main_async() -> None:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    ensure_sdk_paths(project_root)

    from _runtime_setup import ensure_runtime_package_installed
    from codex_app_server import AppServerConfig, AskForApproval, AsyncCodex, SandboxPolicy, TextInput

    ensure_runtime_package_installed(sys.executable, project_root / "docs" / "sdk" / "python")
    approval_policy = AskForApproval.model_validate("never")
    sandbox_policy = SandboxPolicy.model_validate(
        {
            "type": "workspaceWrite",
            "networkAccess": False,
            "readOnlyAccess": {"type": "fullAccess"},
            "writableRoots": [args.worktree],
        }
    )
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    result_file = Path(args.result_file)
    log_file = Path(args.log_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    interrupted = asyncio.Event()
    turn_handle = None

    def on_term(*_unused: object) -> None:
        interrupted.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_term)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: interrupted.set())

    async with AsyncCodex(config=AppServerConfig()) as codex:
        if args.mode == "start":
            thread = await codex.thread_start(
                approval_policy=approval_policy,
                cwd=args.worktree,
                model=args.model,
            )
        else:
            if not args.parent_thread_id:
                raise RuntimeError("fork mode requires --parent-thread-id")
            thread = await codex.thread_fork(
                args.parent_thread_id,
                approval_policy=approval_policy,
                cwd=args.worktree,
                model=args.model,
            )

        turn_handle = await thread.turn(
            TextInput(prompt),
            approval_policy=approval_policy,
            cwd=args.worktree,
            model=args.model,
            sandbox_policy=sandbox_policy,
        )

        final_response = None
        completed_turn = None
        timeout_deadline = loop.time() + args.time_limit
        interrupt_sent = False
        stream = turn_handle.stream()

        try:
            while True:
                if interrupted.is_set() and not interrupt_sent:
                    await turn_handle.interrupt()
                    interrupt_sent = True

                remaining = timeout_deadline - loop.time()
                if remaining <= 0 and not interrupt_sent:
                    await turn_handle.interrupt()
                    interrupt_sent = True
                    remaining = 5

                try:
                    event = await asyncio.wait_for(stream.__anext__(), timeout=max(0.1, remaining))
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    continue

                event_payload = {
                    "method": event.method,
                    "payload": event.payload,
                }
                with log_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event_payload, default=json_default) + "\n")

                if event.method == "turn/completed":
                    completed_turn = event.payload.turn
                    for item in getattr(event.payload.turn, "items", []) or []:
                        raw = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                        if isinstance(raw, dict) and raw.get("type") == "agentMessage":
                            final_response = raw.get("text")
                    break
        finally:
            await stream.aclose()

    result_payload = {
        "thread_id": thread.id,
        "turn_id": None if completed_turn is None else completed_turn.id,
        "status": None if completed_turn is None else json_default(completed_turn.status),
        "final_response": final_response,
        "interrupted": interrupt_sent or interrupted.is_set(),
    }
    result_file.write_text(json.dumps(result_payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
