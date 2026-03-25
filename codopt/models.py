from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class RunConfig:
    run_id: str
    source_repo: str
    effective_source_repo: str
    source_mode: str
    run_root: str
    repo_clone: str
    event_log: str
    state_file: str
    info_text: str
    edit_paths: list[str]
    allow_paths: list[str]
    metric_path: str
    metric_key: str
    lower_is_better: bool
    benchmark_command: str
    test_command: str
    branch_factor: int
    max_agents: int
    rounds: int
    time_limit_seconds: int
    docker_image: str
    model: str
    ui_port: int
    keep_worktrees: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NodeRecord:
    node_id: str
    branch_name: str
    parent_id: str | None
    depth: int
    worktree_path: str
    trusted_commit: str
    thread_id: str | None = None
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    container_name: str | None = None
    score: float | None = None
    metric_text: str | None = None
    test_passed: bool | None = None
    final_response: str | None = None
    error: str | None = None
    result_file: str | None = None
    log_file: str | None = None
    prompt_file: str | None = None
    changed_files: list[str] = field(default_factory=list)
    commit_sha: str | None = None
    pruned: bool = False
    surviving: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunEvent:
    timestamp: str
    type: str
    node_id: str | None
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        event_type: str,
        message: str,
        node_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> "RunEvent":
        return cls(
            timestamp=utc_now(),
            type=event_type,
            node_id=node_id,
            message=message,
            details=details or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def relative_to_repo(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()
