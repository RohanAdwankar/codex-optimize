from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

from codopt._runtime_setup import ensure_runtime_package_installed, sdk_python_dir


def ensure_sdk_paths(project_root: Path) -> None:
    sdk_dir = sdk_python_dir(project_root)
    sdk_src_dir = sdk_dir / "src"
    sdk_root = str(sdk_dir)
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

    from codex_app_server import AppServerConfig, AskForApproval, AsyncCodex, SandboxMode, SandboxPolicy, TextInput
    from codex_app_server.client import _resolve_codex_bin

    ensure_runtime_package_installed(sys.executable, sdk_python_dir(project_root))
    codex_bin = _resolve_codex_bin(AppServerConfig())
    approval_policy = AskForApproval.model_validate("never")
    thread_sandbox = SandboxMode.danger_full_access
    sandbox_policy = SandboxPolicy.model_validate({"type": "dangerFullAccess"})
    thread_config = {
        "sandbox_mode": "danger-full-access",
        "approval_policy": "never",
    }
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    result_file = Path(args.result_file)
    log_file = Path(args.log_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    interrupted = asyncio.Event()
    turn_handle = None
    interrupt_grace_deadline: float | None = None
    thread = None
    completed_turn = None
    final_response = None
    interrupt_sent = False

    def on_term(*_unused: object) -> None:
        interrupted.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_term)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: interrupted.set())

    app_server_config = AppServerConfig(
        codex_bin=str(codex_bin),
        launch_args_override=(
            str(codex_bin),
            "--sandbox",
            "danger-full-access",
            "--ask-for-approval",
            "never",
            "app-server",
            "--listen",
            "stdio://",
        ),
    )

    codex = AsyncCodex(config=app_server_config)
    try:
        async with asyncio.timeout(args.time_limit + 15):
            await codex._ensure_initialized()
            if args.mode == "start":
                thread = await codex.thread_start(
                    approval_policy=approval_policy,
                    config=thread_config,
                    cwd=args.worktree,
                    model=args.model,
                    sandbox=thread_sandbox,
                )
            else:
                if not args.parent_thread_id:
                    raise RuntimeError("fork mode requires --parent-thread-id")
                thread = await codex.thread_fork(
                    args.parent_thread_id,
                    approval_policy=approval_policy,
                    config=thread_config,
                    cwd=args.worktree,
                    model=args.model,
                    sandbox=thread_sandbox,
                )

            turn_handle = await thread.turn(
                TextInput(prompt),
                approval_policy=approval_policy,
                cwd=args.worktree,
                model=args.model,
                sandbox_policy=sandbox_policy,
            )

            timeout_deadline = loop.time() + args.time_limit
            stream = turn_handle.stream()

            try:
                while True:
                    if interrupted.is_set() and not interrupt_sent:
                        await turn_handle.interrupt()
                        interrupt_sent = True
                        interrupt_grace_deadline = loop.time() + 5

                    remaining = timeout_deadline - loop.time()
                    if remaining <= 0 and not interrupt_sent:
                        await turn_handle.interrupt()
                        interrupt_sent = True
                        interrupt_grace_deadline = loop.time() + 5
                        remaining = 5
                    elif interrupt_sent and interrupt_grace_deadline is not None:
                        remaining = interrupt_grace_deadline - loop.time()
                        if remaining <= 0:
                            break

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
                try:
                    await asyncio.wait_for(stream.aclose(), timeout=1)
                except Exception:
                    pass
    except TimeoutError:
        interrupt_sent = True
    finally:
        try:
            await asyncio.wait_for(codex.close(), timeout=1)
        except Exception:
            sync_client = codex._client._sync
            proc = getattr(sync_client, "_proc", None)
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass

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
