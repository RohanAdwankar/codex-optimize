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


def build_image(*, image: str, dockerfile: Path, context: Path) -> None:
    result = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            image,
            "-f",
            str(dockerfile),
            str(context),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to build Docker image {image!r} from {dockerfile}.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def remove_image(image: str) -> None:
    subprocess.run(
        ["docker", "image", "rm", "-f", image],
        text=True,
        capture_output=True,
        check=False,
    )


def detect_project_kind(source_repo: Path) -> str:
    if (source_repo / "stack.yaml").exists() or (source_repo / "cabal.project").exists():
        return "haskell"
    if any(source_repo.glob("*.cabal")) or any(source_repo.rglob("*.hs")):
        return "haskell"
    if (source_repo / "Cargo.toml").exists():
        return "rust"
    if (source_repo / "go.mod").exists():
        return "go"
    if (source_repo / "package.json").exists():
        return "node"
    if (source_repo / "pom.xml").exists() or (source_repo / "build.gradle").exists() or (source_repo / "build.gradle.kts").exists():
        return "java"
    return "python"


def write_auto_dockerfile(source_repo: Path, dockerfile: Path) -> None:
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    rel_requirements = "requirements.txt" if (source_repo / "requirements.txt").exists() else None
    rel_pyproject = "pyproject.toml" if (source_repo / "pyproject.toml").exists() else None
    rel_uv_lock = "uv.lock" if (source_repo / "uv.lock").exists() else None
    rel_package_json = "package.json" if (source_repo / "package.json").exists() else None
    rel_package_lock = "package-lock.json" if (source_repo / "package-lock.json").exists() else None
    rel_cargo = "Cargo.toml" if (source_repo / "Cargo.toml").exists() else None
    rel_go = "go.mod" if (source_repo / "go.mod").exists() else None
    project_kind = detect_project_kind(source_repo)

    if project_kind == "haskell":
        lines = [
            "FROM python:3.12-bookworm",
            "",
            "RUN apt-get update \\",
            "    && apt-get install -y --no-install-recommends git ca-certificates curl build-essential ghc cabal-install xz-utils \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
            "RUN pip install --no-cache-dir uv",
            "",
            "WORKDIR /workspace",
            "",
        ]
        dockerfile.write_text("\n".join(lines), encoding="utf-8")
        return

    if project_kind == "rust":
        lines = [
            "FROM rust:1-bookworm",
            "",
            "RUN apt-get update \\",
            "    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip pkg-config build-essential \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
            "RUN pip install --no-cache-dir uv",
            "",
            "WORKDIR /workspace",
            "",
        ]
        dockerfile.write_text("\n".join(lines), encoding="utf-8")
        return

    if project_kind == "go":
        lines = [
            "FROM golang:1.24-bookworm",
            "",
            "RUN apt-get update \\",
            "    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip build-essential \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
            "RUN pip install --no-cache-dir uv",
            "",
            "WORKDIR /workspace",
            "",
        ]
        dockerfile.write_text("\n".join(lines), encoding="utf-8")
        return

    if project_kind == "node":
        lines = [
            "FROM node:22-bookworm",
            "",
            "RUN apt-get update \\",
            "    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip build-essential \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
            "RUN pip install --no-cache-dir uv",
            "",
            "WORKDIR /opt/codopt-src",
            "",
        ]
    elif project_kind == "java":
        lines = [
            "FROM eclipse-temurin:21-jdk-bookworm",
            "",
            "RUN apt-get update \\",
            "    && apt-get install -y --no-install-recommends git ca-certificates python3 python3-pip build-essential \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
            "RUN pip install --no-cache-dir uv",
            "",
            "WORKDIR /opt/codopt-src",
            "",
        ]
    else:
        lines = [
            "FROM python:3.12-slim",
            "",
            "RUN apt-get update \\",
            "    && apt-get install -y --no-install-recommends git ca-certificates curl build-essential nodejs npm cargo golang-go \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
            "RUN pip install --no-cache-dir uv",
            "",
            "WORKDIR /opt/codopt-src",
            "",
        ]

    copy_targets = [path for path in (rel_requirements, rel_pyproject, rel_uv_lock, rel_package_json, rel_package_lock, rel_cargo, rel_go) if path]
    if copy_targets:
        lines.append(f"COPY {' '.join(copy_targets)} ./")
        lines.append("")

    if rel_requirements:
        lines.extend(
            [
                "RUN uv pip install --system -r requirements.txt || pip install --no-cache-dir -r requirements.txt",
                "",
            ]
        )
    elif rel_pyproject:
        lines.extend(
            [
                "RUN uv pip install --system . || pip install --no-cache-dir . || true",
                "",
            ]
        )

    if rel_package_json:
        npm_install = "npm install"
        if rel_package_lock:
            npm_install = "npm ci"
        lines.extend(
            [
                f"RUN {npm_install} || true",
                'ENV PATH="/opt/codopt-src/node_modules/.bin:${PATH}"',
                'ENV NODE_PATH="/opt/codopt-src/node_modules"',
                "",
            ]
        )

    if rel_cargo:
        lines.extend(
            [
                "RUN cargo fetch || true",
                "",
            ]
        )

    if rel_go:
        lines.extend(
            [
                "RUN go mod download || true",
                "",
            ]
        )

    lines.extend(
        [
            "WORKDIR /workspace",
            "",
        ]
    )
    dockerfile.write_text("\n".join(lines), encoding="utf-8")


def _base_mounts(run_root: Path) -> list[str]:
    return [
        "-v",
        f"{run_root}:{run_root}",
    ]


def _base_env(runtime_root: Path, run_root: Path) -> list[str]:
    codex_home = run_root / "codex_home"
    env = [
        "-e",
        f"PYTHONPATH={runtime_root}",
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


def worker_command(runtime_root: Path, worker_args: list[str]) -> list[str]:
    return [
        "uv",
        "run",
        "--no-project",
        "--python",
        "python3",
        "--with",
        "pydantic",
        "--with",
        "annotated-types",
        "--with",
        "typing-extensions",
        "--with",
        "typing-inspection",
        "python",
        str(runtime_root / "codopt" / "worker.py"),
        *worker_args,
    ]


async def spawn_worker_container(
    *,
    image: str,
    runtime_root: Path,
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
        *_base_mounts(run_root),
        *_base_env(runtime_root, run_root),
        "-w",
        str(worktree),
        image,
        "sh",
        "-lc",
        shell_join(worker_command(runtime_root, worker_args)),
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
    runtime_root: Path,
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
            *_base_mounts(run_root),
            *_base_env(runtime_root, run_root),
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
