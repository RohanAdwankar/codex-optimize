from __future__ import annotations

import argparse
import asyncio
import json
import random
import shutil
import string
from pathlib import Path

from .docker_ops import preflight_image, run_eval_container, spawn_worker_container, stop_container
from .git_ops import (
    changed_files,
    clone_source_repo,
    commit_allowed_changes,
    current_head,
    current_ref,
    delete_branch,
    init_worktree,
    remove_worktree,
    restore_file_from_commit,
)
from .models import NodeRecord, RunConfig, RunEvent, relative_to_repo, utc_now
from .state import StateStore
from .ui import start_ui_server


def build_run_id() -> str:
    token = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"run-{token}"


class CodoptOrchestrator:
    def __init__(self, args: argparse.Namespace, project_root: Path) -> None:
        self.args = args
        self.project_root = project_root.resolve()
        self.source_repo = Path.cwd().resolve()
        self.run_id = args.run_id or build_run_id()
        self.run_root = Path(args.run_root or (Path("/tmp") / "codopt" / self.run_id)).resolve()
        self.repo_clone = self.run_root / "repo"
        self.nodes_dir = self.run_root / "nodes"
        self.logs_dir = self.run_root / "logs"
        self.control_dir = self.run_root / "control"
        self.codex_home = self.run_root / "codex_home"
        self.state_file = self.run_root / "run_state.json"
        self.events_file = self.run_root / "events.jsonl"
        self.edit_paths = [self._normalize_repo_relative(Path(path)) for path in args.edit]
        self.allow_paths = [self._normalize_repo_relative(Path(path)) for path in args.allow_path]
        self.metric_path = self._normalize_repo_relative(Path(args.metric))
        self.info_path = self._normalize_repo_relative(Path(args.info))
        self.info_text = (self.source_repo / self.info_path).read_text(encoding="utf-8")
        self.config = RunConfig(
            run_id=self.run_id,
            source_repo=str(self.source_repo),
            run_root=str(self.run_root),
            repo_clone=str(self.repo_clone),
            event_log=str(self.events_file),
            state_file=str(self.state_file),
            info_text=self.info_text,
            edit_paths=self.edit_paths,
            allow_paths=self.allow_paths,
            metric_path=self.metric_path,
            metric_key=args.metric_key,
            lower_is_better=args.lower_is_better,
            benchmark_command=args.command,
            test_command=args.test,
            branch_factor=args.branch,
            max_agents=args.max_agents,
            rounds=args.rounds,
            time_limit_seconds=args.time,
            docker_image=args.docker_image,
            model=args.model,
            ui_port=args.ui_port,
            keep_worktrees=args.keep_worktrees,
        )
        self.state = StateStore(self.config)
        self.nodes: dict[str, NodeRecord] = {}
        self.baseline_ref = ""

    def _normalize_repo_relative(self, path: Path) -> str:
        resolved = (self.source_repo / path).resolve() if not path.is_absolute() else path.resolve()
        return relative_to_repo(self.source_repo, resolved)

    def prepare(self) -> None:
        if not shutil.which("docker"):
            raise RuntimeError("docker is required for codopt")
        if not (self.source_repo / ".git").exists():
            raise RuntimeError("codopt must be launched from a git repository root")
        self.run_root.mkdir(parents=True, exist_ok=True)
        clone_source_repo(self.source_repo, self.repo_clone)
        self.baseline_ref = current_ref(self.repo_clone)
        self._seed_codex_home()
        preflight_image(self.args.docker_image)
        start_ui_server(self.state_file, self.control_dir, self.args.ui_port, open_browser=not self.args.no_open_ui)
        self.state.set_meta(status="prepared")
        self._add_event("run.prepared", "Prepared run root and UI")

    def _seed_codex_home(self) -> None:
        host_codex_home = Path.home() / ".codex"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        for name in ("auth.json", "config.json"):
            source = host_codex_home / name
            if source.exists():
                shutil.copy2(source, self.codex_home / name)
        self._write_codex_config()

    def _write_codex_config(self) -> None:
        trusted_roots = [
            self.run_root,
            self.repo_clone,
            self.run_root / "worktrees",
        ]
        lines = [
            f'model = {json.dumps(self.args.model)}',
            'model_reasoning_effort = "medium"',
            'approval_policy = "never"',
            'sandbox_mode = "danger-full-access"',
            "",
        ]
        for trusted_root in trusted_roots:
            lines.extend(
                [
                    f"[projects.{json.dumps(str(trusted_root))}]",
                    'trust_level = "trusted"',
                    "",
                ]
            )
        config_text = "\n".join(lines)
        (self.codex_home / "config.toml").write_text(config_text, encoding="utf-8")

    def _add_event(self, event_type: str, message: str, *, node_id: str | None = None, details: dict | None = None) -> None:
        self.state.add_event(RunEvent.build(event_type=event_type, message=message, node_id=node_id, details=details))

    async def run(self) -> None:
        self.prepare()
        baseline_score, baseline_metric_text = self.evaluate_baseline()
        self.state.set_meta(status="running", baseline_score=baseline_score)
        baseline = NodeRecord(
            node_id="baseline",
            branch_name=self.baseline_ref,
            parent_id=None,
            depth=0,
            worktree_path=str(self.repo_clone),
            trusted_commit=current_head(self.repo_clone),
            status="baseline",
            score=baseline_score,
            metric_text=baseline_metric_text,
            test_passed=True,
            surviving=True,
            commit_sha=current_head(self.repo_clone),
        )
        self.nodes[baseline.node_id] = baseline
        self.state.add_node(baseline)

        frontier = [baseline]
        survivor_cap = max(1, self.args.max_agents // self.args.branch)

        for round_index in range(1, self.args.rounds + 1):
            self.state.set_meta(current_round=round_index)
            self._add_event("round.started", f"Starting round {round_index}", details={"parents": [n.node_id for n in frontier]})
            candidates = await self.run_round(frontier, round_index)
            valid = [node for node in candidates if node.score is not None and node.test_passed]
            valid.sort(key=lambda node: (node.score or float("-inf")), reverse=True)
            survivors = valid[:survivor_cap]
            for node in candidates:
                self.state.update_node(node.node_id, surviving=node in survivors)
            if not survivors:
                self._add_event("round.ended", f"Round {round_index} produced no survivors")
                frontier = []
                break
            frontier = survivors
            self._add_event("round.ended", f"Completed round {round_index}", details={"survivors": [n.node_id for n in survivors]})

        final_nodes = frontier
        self.cleanup_branches(final_nodes)
        self.state.set_meta(
            status="completed",
            final_branches=[node.branch_name for node in final_nodes],
            winner_node_id=None if not final_nodes else final_nodes[0].node_id,
        )
        summary = {
            "run_id": self.run_id,
            "repo_clone": str(self.repo_clone),
            "run_root": str(self.run_root),
            "final_branches": [node.branch_name for node in final_nodes],
            "winner": None if not final_nodes else final_nodes[0].to_dict(),
        }
        (self.run_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    async def run_round(self, frontier: list[NodeRecord], round_index: int) -> list[NodeRecord]:
        tasks = []
        for parent in frontier:
            for child_index in range(self.args.branch):
                tasks.append(self.run_node(parent, round_index, child_index))
        return await asyncio.gather(*tasks)

    def node_paths(self, node_id: str) -> tuple[Path, Path, Path]:
        node_dir = self.nodes_dir / node_id
        prompt_file = node_dir / "prompt.txt"
        result_file = node_dir / "result.json"
        log_file = node_dir / "agent.jsonl"
        node_dir.mkdir(parents=True, exist_ok=True)
        return prompt_file, result_file, log_file

    def build_prompt(self) -> str:
        edit_paths = "\n".join(f"- {path}" for path in self.edit_paths)
        return f"""You are optimizing a software metric.

Goal:
- Improve the numeric score produced in `{self.metric_path}`.

Primary optimization targets:
{edit_paths}

Hard rules:
- Do not create commits, branches, or manipulate git history.
- Do not intentionally modify `{self.metric_path}` except as an incidental artifact of running the benchmark.
- You may add or modify supporting files when that helps the optimization.
- Prefer incremental, correctness-preserving improvements over risky rewrites.
- Avoid spending your turn budget on running tests or benchmarks yourself unless it is essential to unblock a specific edit.

Background information:
{self.info_text}
"""

    async def run_node(self, parent: NodeRecord, round_index: int, child_index: int) -> NodeRecord:
        node_id = f"r{round_index}_p{parent.node_id}_{child_index}"
        branch_name = node_id.replace("_", "-")
        worktree_path = self.run_root / "worktrees" / branch_name
        init_worktree(self.repo_clone, parent.branch_name, branch_name, worktree_path)
        prompt_file, result_file, log_file = self.node_paths(node_id)
        prompt_file.write_text(self.build_prompt(), encoding="utf-8")

        node = NodeRecord(
            node_id=node_id,
            branch_name=branch_name,
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            worktree_path=str(worktree_path),
            trusted_commit=parent.trusted_commit,
            status="running",
            started_at=utc_now(),
            result_file=str(result_file),
            log_file=str(log_file),
            prompt_file=str(prompt_file),
        )
        self.nodes[node_id] = node
        self.state.add_node(node)
        self._add_event("node.started", f"Started node {node_id}", node_id=node_id, details={"branch": branch_name})

        worker_mode = "start" if parent.node_id == "baseline" else "fork"
        container_name = f"codopt-{self.run_id}-{branch_name}"[:63]
        self.state.update_node(node_id, container_name=container_name)
        proc = await spawn_worker_container(
            image=self.args.docker_image,
            project_root=self.project_root,
            run_root=self.run_root,
            worktree=worktree_path,
            container_name=container_name,
            worker_args=[
                "--project-root",
                str(self.project_root),
                "--worktree",
                str(worktree_path),
                "--mode",
                worker_mode,
                "--prompt-file",
                str(prompt_file),
                "--result-file",
                str(result_file),
                "--log-file",
                str(log_file),
                "--model",
                self.args.model,
                "--time-limit",
                str(self.args.time),
                *([] if parent.thread_id is None else ["--parent-thread-id", parent.thread_id]),
            ],
        )

        pruned = False
        while True:
            if (self.control_dir / f"{node_id}.prune").exists():
                pruned = True
                await stop_container(container_name)
                break
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
                break
            except TimeoutError:
                continue

        stdout, stderr = await proc.communicate()
        return_code = proc.returncode
        if stdout:
            (self.logs_dir / f"{node_id}.stdout.log").parent.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / f"{node_id}.stdout.log").write_bytes(stdout)
        if stderr:
            (self.logs_dir / f"{node_id}.stderr.log").parent.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / f"{node_id}.stderr.log").write_bytes(stderr)

        result_payload = {}
        if result_file.exists():
            result_payload = json.loads(result_file.read_text(encoding="utf-8"))
        elif return_code == 0 and not pruned:
            self.state.update_node(
                node_id,
                status="failed",
                finished_at=utc_now(),
                error="worker exited without result file",
            )
            self._add_event("node.failed", f"Worker exited without result for {node_id}", node_id=node_id)
            return self.nodes[node_id]

        if return_code not in (0, None) and not pruned:
            worker_error = (stderr.decode("utf-8", errors="replace") if stderr else "").strip()
            self.state.update_node(
                node_id,
                status="failed",
                finished_at=utc_now(),
                error=f"worker failed with exit code {return_code}\n{worker_error}",
            )
            self._add_event("node.failed", f"Worker failed for {node_id}", node_id=node_id, details={"exit_code": return_code})
            return self.nodes[node_id]

        thread_id = result_payload.get("thread_id")
        self.state.update_node(
            node_id,
            thread_id=thread_id,
            final_response=result_payload.get("final_response"),
            status="pruned" if pruned else "evaluating",
            pruned=pruned,
        )

        if pruned:
            self._add_event("node.pruned", f"Pruned node {node_id}", node_id=node_id)
            self.state.update_node(node_id, finished_at=utc_now(), error="manually pruned")
            return self.nodes[node_id]

        node_record = self.evaluate_node(self.nodes[node_id])
        self._add_event("node.completed", f"Completed node {node_id}", node_id=node_id, details={"score": node_record.score})
        return node_record

    def evaluate_baseline(self) -> tuple[float, str]:
        restore_file_from_commit(self.repo_clone, self.repo_clone, current_head(self.repo_clone), self.metric_path)
        code, _out, err = run_eval_container(
            image=self.args.docker_image,
            project_root=self.project_root,
            run_root=self.run_root,
            worktree=self.repo_clone,
            command=self.args.command,
        )
        if code != 0:
            raise RuntimeError(f"Baseline benchmark failed:\n{err}")
        metric_text = (self.repo_clone / self.metric_path).read_text(encoding="utf-8")
        score = self.parse_metric(metric_text)
        restore_file_from_commit(self.repo_clone, self.repo_clone, current_head(self.repo_clone), self.metric_path)
        code, _out, err = run_eval_container(
            image=self.args.docker_image,
            project_root=self.project_root,
            run_root=self.run_root,
            worktree=self.repo_clone,
            command=self.args.test,
        )
        if code != 0:
            raise RuntimeError(f"Baseline tests failed:\n{err}")
        return score, metric_text

    def parse_metric(self, metric_text: str) -> float:
        stripped = metric_text.strip()
        try:
            if stripped.startswith("{"):
                payload = json.loads(stripped)
                raw_value = float(payload[self.args.metric_key])
            else:
                raw_value = float(stripped)
            return -raw_value if self.args.lower_is_better else raw_value
        except Exception as exc:
            if stripped.startswith("{"):
                detail = f"expected JSON key {self.args.metric_key!r}"
            else:
                detail = "expected a plain numeric value"
            raise RuntimeError(
                f"Unable to parse metric file {self.metric_path}: {detail}. Raw contents: {metric_text!r}"
            ) from exc

    def evaluate_node(self, node: NodeRecord) -> NodeRecord:
        worktree = Path(node.worktree_path)
        files = changed_files(worktree)
        self.state.update_node(node.node_id, changed_files=files)
        effective_files = [path for path in files if path != self.metric_path]
        if not effective_files:
            self.state.update_node(
                node.node_id,
                status="failed",
                finished_at=utc_now(),
                error="node produced no code changes",
            )
            return self.nodes[node.node_id]

        restore_file_from_commit(self.repo_clone, worktree, node.trusted_commit, self.metric_path)
        code, stdout, stderr = run_eval_container(
            image=self.args.docker_image,
            project_root=self.project_root,
            run_root=self.run_root,
            worktree=worktree,
            command=self.args.command,
        )
        if code != 0:
            self.state.update_node(node.node_id, status="failed", finished_at=utc_now(), error=f"benchmark failed\n{stdout}\n{stderr}")
            return self.nodes[node.node_id]

        metric_text = (worktree / self.metric_path).read_text(encoding="utf-8")
        score = self.parse_metric(metric_text)

        restore_file_from_commit(self.repo_clone, worktree, node.trusted_commit, self.metric_path)
        code, stdout, stderr = run_eval_container(
            image=self.args.docker_image,
            project_root=self.project_root,
            run_root=self.run_root,
            worktree=worktree,
            command=self.args.test,
        )
        if code != 0:
            self.state.update_node(
                node.node_id,
                status="failed",
                score=score,
                metric_text=metric_text,
                test_passed=False,
                finished_at=utc_now(),
                error=f"tests failed\n{stdout}\n{stderr}",
            )
            return self.nodes[node.node_id]

        commit_sha = commit_allowed_changes(
            worktree,
            effective_files,
            f"codopt: candidate {node.node_id}",
        )
        if commit_sha is None:
            commit_sha = node.trusted_commit
        self.state.update_node(
            node.node_id,
            status="completed",
            score=score,
            metric_text=metric_text,
            test_passed=True,
            commit_sha=commit_sha,
            trusted_commit=commit_sha,
            finished_at=utc_now(),
        )
        self.nodes[node.node_id].trusted_commit = commit_sha
        return self.nodes[node.node_id]

    def cleanup_branches(self, final_nodes: list[NodeRecord]) -> None:
        survivor_branches = {node.branch_name for node in final_nodes}
        survivor_ids = {node.node_id for node in final_nodes}
        for node in list(self.nodes.values()):
            if node.node_id == "baseline":
                continue
            worktree = Path(node.worktree_path)
            if not self.args.keep_worktrees:
                remove_worktree(self.repo_clone, worktree)
            if node.branch_name not in survivor_branches:
                delete_branch(self.repo_clone, node.branch_name)
            elif node.node_id in survivor_ids:
                self.state.update_node(node.node_id, surviving=True)


async def run_codopt(args: argparse.Namespace, project_root: Path) -> CodoptOrchestrator:
    orchestrator = CodoptOrchestrator(args, project_root)
    await orchestrator.run()
    return orchestrator
