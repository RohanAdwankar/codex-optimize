from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from pathlib import Path


def shell_join(parts: list[str]) -> str:
    return shlex.join(parts)


def preflight_image(image: str) -> None:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            image,
            "sh",
            "-lc",
            "command -v python3 >/dev/null && command -v git >/dev/null && command -v uv >/dev/null",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Docker image {image!r} failed codopt preflight. The image must provide python3, git, and uv.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def _base_mounts(project_root: Path, run_root: Path) -> list[str]:
    return [
        "-v",
        f"{project_root}:{project_root}:ro",
        "-v",
        f"{run_root}:{run_root}",
    ]


def _base_env(project_root: Path, run_root: Path) -> list[str]:
    codex_home = run_root / "codex_home"
    py_path = f"{project_root}:{project_root / 'docs/sdk/python'}:{project_root / 'docs/sdk/python/src'}"
    env = [
        "-e",
        f"PYTHONPATH={py_path}",
        "-e",
        f"CODEX_HOME={codex_home}",
        "-e",
        f"HOME={run_root}",
    ]
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        value = os.environ.get(key)
        if value:
            env.extend(["-e", f"{key}={value}"])
    return env


def worker_command(project_root: Path, worker_args: list[str]) -> list[str]:
    return [
        "uv",
        "run",
        "--with",
        "pydantic",
        "--with",
        "annotated-types",
        "--with",
        "typing-extensions",
        "--with",
        "typing-inspection",
        "python",
        "-m",
        "codopt.worker",
        *worker_args,
    ]


async def spawn_worker_container(
    *,
    image: str,
    project_root: Path,
    run_root: Path,
    worktree: Path,
    container_name: str,
    worker_args: list[str],
) -> asyncio.subprocess.Process:
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        *_base_mounts(project_root, run_root),
        *_base_env(project_root, run_root),
        "-w",
        str(worktree),
        image,
        "sh",
        "-lc",
        shell_join(worker_command(project_root, worker_args)),
    ]
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def stop_container(container_name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "stop",
        "-t",
        "2",
        container_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


def run_eval_container(
    *,
    image: str,
    project_root: Path,
    run_root: Path,
    worktree: Path,
    command: str,
) -> tuple[int, str, str]:
    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            *_base_mounts(project_root, run_root),
            *_base_env(project_root, run_root),
            "-w",
            str(worktree),
            image,
            "sh",
            "-lc",
            command,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr
