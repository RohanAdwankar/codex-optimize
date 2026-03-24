from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
SDK_PYTHON_DIR = REPO_ROOT / "docs" / "sdk" / "python"
SDK_EXAMPLES_DIR = SDK_PYTHON_DIR / "examples"

for path in (SDK_EXAMPLES_DIR, SDK_PYTHON_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from _bootstrap import ensure_local_sdk_src, runtime_config  # type: ignore  # noqa: E402

ensure_local_sdk_src()

from codex_app_server import AskForApproval, AsyncCodex, SandboxPolicy, TextInput  # type: ignore  # noqa: E402


PAYLOAD_FILE = "payload.txt"
APPROVAL_POLICY = AskForApproval.model_validate("never")


PROMPT = f"""Edit only `{PAYLOAD_FILE}`.

Rules:
- The file must contain only raw lowercase `x` characters and nothing else.
- Do not leave a trailing newline.
- If the file is empty, make it contain exactly `x`.
- If the file contains exactly `x`, make it contain exactly `xx`.
- If the file already contains exactly `xx`, leave it unchanged.
- Do not edit any other file.
- Do not create commits or branches.
- When finished, reply with the file contents.
"""


@dataclass(slots=True)
class BranchNode:
    branch_name: str
    worktree_path: str
    thread_id: str
    depth: int
    payload: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal Codex SDK branch/fork harness.")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--branch-factor", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--run-name", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    return parser.parse_args()


def run_cmd(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.rstrip("\n")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_workspace_write_policy(worktree: Path) -> SandboxPolicy:
    return SandboxPolicy.model_validate(
        {
            "type": "workspaceWrite",
            "networkAccess": False,
            "readOnlyAccess": {"type": "fullAccess"},
            "writableRoots": [str(worktree)],
        }
    )


def init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_cmd(["git", "init", "-b", "main"], cwd=repo_dir)
    run_cmd(["git", "config", "user.name", "codex-branch-simple"], cwd=repo_dir)
    run_cmd(["git", "config", "user.email", "codex-branch-simple@example.com"], cwd=repo_dir)
    (repo_dir / PAYLOAD_FILE).write_text("", encoding="utf-8")
    run_cmd(["git", "add", PAYLOAD_FILE], cwd=repo_dir)
    run_cmd(["git", "commit", "-m", "Initial empty payload"], cwd=repo_dir)


def add_worktree(repo_dir: Path, source_ref: str, branch_name: str, worktree_path: Path) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_path), source_ref],
        cwd=repo_dir,
    )
    run_cmd(["git", "config", "user.name", "codex-branch-simple"], cwd=worktree_path)
    run_cmd(["git", "config", "user.email", "codex-branch-simple@example.com"], cwd=worktree_path)


def commit_payload_if_needed(worktree_path: Path, message: str) -> None:
    changed = run_cmd(["git", "status", "--porcelain"], cwd=worktree_path)
    if not changed:
        return

    changed_files = []
    for line in changed.splitlines():
        candidate = line[3:]
        if candidate:
            changed_files.append(candidate)

    if changed_files != [PAYLOAD_FILE]:
        raise RuntimeError(
            f"Unexpected file edits in {worktree_path}: expected only {PAYLOAD_FILE}, got {changed_files}"
        )

    run_cmd(["git", "add", PAYLOAD_FILE], cwd=worktree_path)
    run_cmd(["git", "commit", "-m", message], cwd=worktree_path)


def read_payload(worktree_path: Path) -> str:
    return (worktree_path / PAYLOAD_FILE).read_text(encoding="utf-8")


async def run_root_agent(
    *,
    model: str,
    worktree_path: Path,
    logs_dir: Path,
) -> BranchNode:
    async with AsyncCodex(config=runtime_config()) as codex:
        thread = await codex.thread_start(
            approval_policy=APPROVAL_POLICY,
            cwd=str(worktree_path),
            model=model,
        )
        result = await thread.run(
            TextInput(PROMPT),
            approval_policy=APPROVAL_POLICY,
            cwd=str(worktree_path),
            model=model,
            sandbox_policy=build_workspace_write_policy(worktree_path),
        )

    payload = read_payload(worktree_path)
    if payload != "x":
        raise RuntimeError(f"Root branch expected payload 'x', got {payload!r}")

    commit_payload_if_needed(worktree_path, "Root agent wrote payload")
    write_json(
        logs_dir / "root.json",
        {
            "branch_name": "node_root",
            "worktree_path": str(worktree_path),
            "thread_id": thread.id,
            "final_response": result.final_response,
            "item_count": len(result.items),
            "payload": payload,
        },
    )
    return BranchNode(
        branch_name="node_root",
        worktree_path=str(worktree_path),
        thread_id=thread.id,
        depth=0,
        payload=payload,
    )


async def run_child_agent(
    *,
    model: str,
    repo_dir: Path,
    parent: BranchNode,
    child_index: int,
    logs_dir: Path,
    semaphore: asyncio.Semaphore,
) -> BranchNode:
    child_depth = parent.depth + 1
    child_branch_name = f"node_d{child_depth}_{parent.branch_name}_{child_index}"
    child_worktree_path = repo_dir.parent / "worktrees" / child_branch_name

    add_worktree(repo_dir, parent.branch_name, child_branch_name, child_worktree_path)

    async with semaphore:
        async with AsyncCodex(config=runtime_config()) as codex:
            thread = await codex.thread_fork(
                parent.thread_id,
                approval_policy=APPROVAL_POLICY,
                cwd=str(child_worktree_path),
                model=model,
            )
            result = await thread.run(
                TextInput(PROMPT),
                approval_policy=APPROVAL_POLICY,
                cwd=str(child_worktree_path),
                model=model,
                sandbox_policy=build_workspace_write_policy(child_worktree_path),
            )

    payload = read_payload(child_worktree_path)
    expected = "xx" if child_depth >= 1 else "x"
    if payload != expected:
        raise RuntimeError(
            f"Branch {child_branch_name} expected payload {expected!r}, got {payload!r}"
        )

    commit_payload_if_needed(
        child_worktree_path,
        f"Agent depth {child_depth} updated payload from {parent.branch_name}",
    )
    write_json(
        logs_dir / f"{child_branch_name}.json",
        {
            "branch_name": child_branch_name,
            "parent_branch": parent.branch_name,
            "parent_thread_id": parent.thread_id,
            "worktree_path": str(child_worktree_path),
            "thread_id": thread.id,
            "final_response": result.final_response,
            "item_count": len(result.items),
            "payload": payload,
        },
    )
    return BranchNode(
        branch_name=child_branch_name,
        worktree_path=str(child_worktree_path),
        thread_id=thread.id,
        depth=child_depth,
        payload=payload,
    )


async def main_async() -> None:
    args = parse_args()

    run_root = ROOT / "runs" / args.run_name
    repo_dir = run_root / "repo"
    worktrees_dir = run_root / "worktrees"
    logs_dir = run_root / "logs"

    init_repo(repo_dir)
    root_worktree = worktrees_dir / "node_root"
    add_worktree(repo_dir, "main", "node_root", root_worktree)

    root_node = await run_root_agent(model=args.model, worktree_path=root_worktree, logs_dir=logs_dir)

    frontier = [root_node]
    semaphore = asyncio.Semaphore(args.concurrency)

    for _round in range(1, args.rounds + 1):
        tasks = []
        for parent in frontier:
            for child_index in range(args.branch_factor):
                tasks.append(
                    run_child_agent(
                        model=args.model,
                        repo_dir=repo_dir,
                        parent=parent,
                        child_index=child_index,
                        logs_dir=logs_dir,
                        semaphore=semaphore,
                    )
                )
        frontier = await asyncio.gather(*tasks)

    final_nodes = frontier
    if len(final_nodes) != args.branch_factor ** args.rounds:
        raise RuntimeError(
            f"Expected {args.branch_factor ** args.rounds} final nodes, got {len(final_nodes)}"
        )

    for node in final_nodes:
        if node.payload != "xx":
            raise RuntimeError(f"Leaf branch {node.branch_name} ended with {node.payload!r}, expected 'xx'")

    git_branches = run_cmd(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        cwd=repo_dir,
    ).splitlines()
    final_branch_names = sorted(node.branch_name for node in final_nodes)
    missing = [name for name in final_branch_names if name not in git_branches]
    if missing:
        raise RuntimeError(f"Missing expected leaf branches: {missing}")

    summary = {
        "run_root": str(run_root),
        "model": args.model,
        "branch_factor": args.branch_factor,
        "rounds": args.rounds,
        "final_branch_count": len(final_nodes),
        "expected_final_branch_count": args.branch_factor ** args.rounds,
        "leaf_payloads": {node.branch_name: node.payload for node in final_nodes},
        "leaf_nodes": [asdict(node) for node in final_nodes],
    }
    write_json(run_root / "summary.json", summary)

    print(f"Run root: {run_root}")
    print(f"Final branches: {len(final_nodes)}")
    print("Verification: PASS")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
