from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def run_git(repo: Path, args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout.rstrip("\n")


def clone_source_repo(source_repo: Path, target_repo: Path) -> None:
    target_repo.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--local", "--no-hardlinks", str(source_repo), str(target_repo)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def configure_user(repo: Path, *, name: str = "codopt", email: str = "codopt@example.com") -> None:
    run_git(repo, ["config", "user.name", name])
    run_git(repo, ["config", "user.email", email])


def changed_paths_against_head(repo: Path) -> list[str]:
    output = run_git(repo, ["diff", "--name-only", "HEAD", "--"])
    return [line for line in output.splitlines() if line]


def is_tracked(repo: Path, rel_path: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel_path],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def untracked_files_under(repo: Path, rel_dir: str) -> list[str]:
    root = (repo / rel_dir).resolve()
    if not root.exists() or not root.is_dir():
        return []
    files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.resolve().relative_to(repo.resolve()).as_posix()
        if not is_tracked(repo, rel_path):
            files.append(rel_path)
    return files


def commit_all(repo: Path, message: str) -> str | None:
    run_git(repo, ["add", "-A"])
    status = run_git(repo, ["status", "--porcelain"])
    if not status:
        return None
    run_git(repo, ["commit", "-m", message])
    return current_head(repo)


def init_worktree(repo_root: Path, source_ref: str, branch_name: str, worktree_path: Path) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    run_git(repo_root, ["worktree", "add", "--force", "-b", branch_name, str(worktree_path), source_ref])
    configure_user(worktree_path)


def current_head(repo: Path) -> str:
    return run_git(repo, ["rev-parse", "HEAD"])


def current_ref(repo: Path) -> str:
    branch = run_git(repo, ["branch", "--show-current"], check=False).strip()
    return branch or current_head(repo)


def changed_files(worktree: Path) -> list[str]:
    output = run_git(worktree, ["status", "--porcelain"])
    files: list[str] = []
    for line in output.splitlines():
        if len(line) >= 4:
            files.append(line[3:])
    return files


def file_exists_in_commit(repo: Path, commit: str, rel_path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:{rel_path}"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def restore_file_from_commit(repo: Path, worktree: Path, commit: str, rel_path: str) -> None:
    target = worktree / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if file_exists_in_commit(repo, commit, rel_path):
        result = subprocess.run(
            ["git", "show", f"{commit}:{rel_path}"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        target.write_bytes(result.stdout)
        return
    if target.exists():
        target.unlink()


def commit_allowed_changes(worktree: Path, rel_paths: list[str], message: str) -> str | None:
    existing = [rel for rel in rel_paths if (worktree / rel).exists()]
    if existing:
        run_git(worktree, ["add", "--", *existing])
    deleted = [rel for rel in rel_paths if not (worktree / rel).exists()]
    if deleted:
        run_git(worktree, ["rm", "--cached", "--ignore-unmatch", "--", *deleted], check=False)
    status = run_git(worktree, ["status", "--porcelain"])
    if not status:
        return None
    run_git(worktree, ["commit", "-m", message])
    return current_head(worktree)


def remove_worktree(repo_root: Path, worktree: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)


def delete_branch(repo_root: Path, branch_name: str) -> None:
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
