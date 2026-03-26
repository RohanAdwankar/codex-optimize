from __future__ import annotations

import argparse
import asyncio
import json
import random
import shlex
import shutil
import string
import subprocess
from pathlib import Path
from typing import Any

from .docker_ops import build_image, preflight_image, remove_image, run_eval_container, spawn_worker_container, stop_container, write_auto_dockerfile
from .git_ops import (
    changed_files,
    changed_paths_against_head,
    clone_source_repo,
    commit_all,
    commit_allowed_changes,
    configure_user,
    current_head,
    current_ref,
    delete_branch,
    init_worktree,
    is_tracked,
    remove_worktree,
    restore_file_from_commit,
    untracked_files_under,
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
        self.effective_source_repo = self.source_repo
        self.run_id = args.run_id or build_run_id()
        self.run_root = Path(args.run_root or (Path("/tmp") / "codopt" / self.run_id)).resolve()
        self.source_snapshot_repo = self.run_root / "source_repo"
        self.repo_clone = self.run_root / "repo"
        self.nodes_dir = self.run_root / "nodes"
        self.logs_dir = self.run_root / "logs"
        self.control_dir = self.run_root / "control"
        self.codex_home = self.run_root / "codex_home"
        self.inputs_dir = self.run_root / "inputs"
        self.runtime_dir = self.run_root / "runtime"
        self.validation_dir = self.run_root / "validation"
        self.state_file = self.run_root / "run_state.json"
        self.events_file = self.run_root / "events.jsonl"
        self.metric_path = self._normalize_repo_relative(Path(args.metric))
        self.edit_paths = [self._normalize_repo_relative(Path(path)) for path in args.edit]
        self.allow_paths = [self._normalize_repo_relative_or_absolute(Path(path)) for path in args.allow_path]
        self.info_text = self._load_info_text()
        self.command_file_source = self._resolve_optional_path(args.command_file)
        self.test_file_source = self._resolve_optional_path(args.test_file)
        self.benchmark_command = args.command
        self.test_command = args.test
        self.config = RunConfig(
            run_id=self.run_id,
            source_repo=str(self.source_repo),
            effective_source_repo=str(self.effective_source_repo),
            source_mode=args.source_mode,
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
            benchmark_command=self.benchmark_command,
            test_command=self.test_command,
            branch_factor=args.branch,
            max_agents=args.max_agents,
            rounds=args.rounds,
            time_limit_seconds=args.time,
            docker_image=args.docker_image or "",
            model=args.model,
            ui_port=args.ui_port,
            keep_worktrees=args.keep_worktrees,
        )
        self.state = StateStore(self.config)
        self.nodes: dict[str, NodeRecord] = {}
        self.baseline_ref = ""
        self.runtime_image = ""
        self.runtime_image_owned = False
        self.benchmark_script_copy: Path | None = None
        self.test_script_copy: Path | None = None

    def _normalize_repo_relative(self, path: Path) -> str:
        resolved = (self.source_repo / path).resolve() if not path.is_absolute() else path.resolve()
        return relative_to_repo(self.source_repo, resolved)

    def _normalize_repo_relative_or_absolute(self, path: Path) -> str:
        resolved = (self.source_repo / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            return relative_to_repo(self.source_repo, resolved)
        except ValueError:
            return str(resolved)

    def _resolve_optional_path(self, raw: str | None) -> Path | None:
        if not raw:
            return None
        path = Path(raw).expanduser()
        return (self.source_repo / path).resolve() if not path.is_absolute() else path.resolve()

    def _repo_relative_path(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return path.resolve().relative_to(self.source_repo).as_posix()
        except ValueError:
            return None

    def _load_info_text(self) -> str:
        if getattr(self.args, "info_text", None):
            return self.args.info_text
        if not getattr(self.args, "info", None):
            raise RuntimeError("one of --info or --info-text is required")
        resolved = self._resolve_optional_path(self.args.info)
        if resolved is None:
            raise RuntimeError("missing info path")
        return resolved.read_text(encoding="utf-8")

    def prepare(self) -> None:
        if not shutil.which("docker"):
            raise RuntimeError("docker is required for codopt")
        if not (self.source_repo / ".git").exists():
            raise RuntimeError("codopt must be launched from a git repository root")
        if (self.run_root / "repo").exists():
            raise RuntimeError(f"run root {self.run_root} already contains a previous run; choose a new --run-root or remove it")
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._materialize_inputs()
        self._materialize_runtime()
        self.effective_source_repo = self._prepare_effective_source_repo()
        self.config.effective_source_repo = str(self.effective_source_repo)
        self.config.info_text = self.info_text
        self.config.benchmark_command = self.benchmark_command or ""
        self.config.test_command = self.test_command or ""
        clone_source_repo(self.effective_source_repo, self.repo_clone)
        self.baseline_ref = current_ref(self.repo_clone)
        self._seed_codex_home()
        self.runtime_image = self._resolve_runtime_image()
        self.config.docker_image = self.runtime_image
        preflight_image(self.runtime_image)
        if not self.args.no_open_ui:
            start_ui_server(self.state_file, self.control_dir, self.args.ui_port, open_browser=True)
        self.state.set_meta(status="prepared")
        self._add_event(
            "run.prepared",
            "Prepared run root and UI",
            details={
                "docker_image": self.runtime_image,
                "source_mode": self.args.source_mode,
                "effective_source_repo": str(self.effective_source_repo),
            },
        )

    def _materialize_inputs(self) -> None:
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        (self.inputs_dir / "info.txt").write_text(self.info_text, encoding="utf-8")
        if self.command_file_source is not None:
            repo_rel = self._repo_relative_path(self.command_file_source)
            if repo_rel is not None:
                self.benchmark_command = f"sh -eu {shlex.quote(repo_rel)}"
            else:
                benchmark_copy = self.inputs_dir / "benchmark.sh"
                benchmark_copy.write_text(self.command_file_source.read_text(encoding="utf-8"), encoding="utf-8")
                benchmark_copy.chmod(0o755)
                self.benchmark_script_copy = benchmark_copy
                self.benchmark_command = f"sh -eu {shlex.quote(str(benchmark_copy))}"
        if self.test_file_source is not None:
            repo_rel = self._repo_relative_path(self.test_file_source)
            if repo_rel is not None:
                self.test_command = f"sh -eu {shlex.quote(repo_rel)}"
            else:
                test_copy = self.inputs_dir / "test.sh"
                test_copy.write_text(self.test_file_source.read_text(encoding="utf-8"), encoding="utf-8")
                test_copy.chmod(0o755)
                self.test_script_copy = test_copy
                self.test_command = f"sh -eu {shlex.quote(str(test_copy))}"

    def _materialize_runtime(self) -> None:
        package_dir = self.project_root / "codopt"
        runtime_package_dir = self.runtime_dir / "codopt"
        shutil.copytree(package_dir, runtime_package_dir, dirs_exist_ok=True)

    def _resolve_runtime_image(self) -> str:
        if self.args.docker_image and self.args.dockerfile:
            raise RuntimeError("pass at most one of --docker-image or --dockerfile")
        if self.args.docker_image:
            self.runtime_image_owned = False
            return self.args.docker_image

        image = f"codopt-auto-{self.run_id}"
        self.runtime_image_owned = True
        dockerfile = self.run_root / "auto.Dockerfile"
        if self.args.dockerfile:
            source_dockerfile = Path(self.args.dockerfile).expanduser()
            if source_dockerfile.is_absolute():
                resolved = source_dockerfile.resolve()
            else:
                resolved = (self.effective_source_repo / source_dockerfile).resolve()
            shutil.copy2(resolved, dockerfile)
        else:
            write_auto_dockerfile(self.effective_source_repo, dockerfile)
        build_image(image=image, dockerfile=dockerfile, context=self.effective_source_repo)
        return image

    def _prepare_effective_source_repo(self) -> Path:
        if self.args.source_mode == "head":
            return self.source_repo

        clone_source_repo(self.source_repo, self.source_snapshot_repo)
        configure_user(self.source_snapshot_repo)

        for rel_path in changed_paths_against_head(self.source_repo):
            source_path = self.source_repo / rel_path
            snapshot_path = self.source_snapshot_repo / rel_path
            if source_path.exists():
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, snapshot_path)
            elif snapshot_path.exists():
                snapshot_path.unlink()

        for candidate in (self.args.info, self.args.dockerfile, self.args.command_file, self.args.test_file):
            if not candidate:
                continue
            candidate_path = Path(candidate).expanduser()
            resolved = (self.source_repo / candidate_path).resolve() if not candidate_path.is_absolute() else candidate_path.resolve()
            try:
                rel_path = resolved.relative_to(self.source_repo)
            except ValueError:
                continue
            rel_str = rel_path.as_posix()
            if is_tracked(self.source_repo, rel_str):
                continue
            if not resolved.exists():
                continue
            snapshot_path = self.source_snapshot_repo / rel_path
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, snapshot_path)
            if rel_str in filter(None, [self._repo_relative_path(self.command_file_source), self._repo_relative_path(self.test_file_source)]):
                for untracked_rel in untracked_files_under(self.source_repo, rel_path.parent.as_posix() or "."):
                    untracked_source = self.source_repo / untracked_rel
                    untracked_target = self.source_snapshot_repo / untracked_rel
                    untracked_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(untracked_source, untracked_target)

        commit_all(self.source_snapshot_repo, "codopt: working tree snapshot")
        return self.source_snapshot_repo

    def _cleanup_runtime_image(self) -> None:
        if self.runtime_image_owned and self.runtime_image:
            remove_image(self.runtime_image)

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
        (self.codex_home / "config.toml").write_text("\n".join(lines), encoding="utf-8")

    def _add_event(self, event_type: str, message: str, *, node_id: str | None = None, details: dict | None = None) -> None:
        self.state.add_event(RunEvent.build(event_type=event_type, message=message, node_id=node_id, details=details))
        self._print_progress(event_type, message, node_id=node_id, details=details or {})

    def _print_progress(self, event_type: str, message: str, *, node_id: str | None, details: dict[str, Any]) -> None:
        interesting = {
            "run.prepared",
            "round.started",
            "round.ended",
            "node.started",
            "node.completed",
            "node.failed",
            "node.pruned",
        }
        if event_type not in interesting:
            return
        line = f"[codopt] {message}"
        if node_id is not None:
            line += f" ({node_id})"
        if event_type == "run.prepared":
            line += f" | run_root={self.run_root} | source_mode={self.args.source_mode}"
            if not self.args.no_open_ui:
                line += f" | ui=http://127.0.0.1:{self.args.ui_port}"
            else:
                line += f" | inspect with: codopt ui --run-root {self.run_root}"
        elif event_type == "node.completed" and "score" in details:
            line += f" | score={details['score']}"
        elif event_type == "round.ended" and details.get("survivors"):
            line += f" | survivors={','.join(details['survivors'])}"
        print(line, flush=True)

    def _run_host_command(self, worktree: Path, command: str) -> tuple[int, str, str]:
        proc = subprocess.run(
            ["sh", "-lc", command],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _prepare_validation_clone(self, name: str) -> Path:
        repo_path = self.validation_dir / name / "repo"
        clone_source_repo(self.effective_source_repo, repo_path)
        return repo_path

    def _read_metric_from(self, worktree: Path) -> tuple[float, str]:
        metric_text = (worktree / self.metric_path).read_text(encoding="utf-8")
        return self.parse_metric(metric_text), metric_text

    def validate_host_commands(self) -> dict[str, Any]:
        host_repo = self._prepare_validation_clone("host")
        restore_file_from_commit(host_repo, host_repo, current_head(host_repo), self.metric_path)
        bench_code, bench_out, bench_err = self._run_host_command(host_repo, self.benchmark_command)
        if bench_code != 0:
            raise RuntimeError(f"Host benchmark failed:\n{bench_out}\n{bench_err}")
        baseline_score, metric_text = self._read_metric_from(host_repo)
        restore_file_from_commit(host_repo, host_repo, current_head(host_repo), self.metric_path)
        test_code, test_out, test_err = self._run_host_command(host_repo, self.test_command)
        if test_code != 0:
            raise RuntimeError(f"Host tests failed:\n{test_out}\n{test_err}")
        return {
            "stage": "host",
            "score": baseline_score,
            "metric_text": metric_text,
            "worktree": str(host_repo),
            "benchmark_script": None if self.benchmark_script_copy is None else str(self.benchmark_script_copy),
            "test_script": None if self.test_script_copy is None else str(self.test_script_copy),
        }

    def validate_container_commands(self) -> dict[str, Any]:
        container_repo = self._prepare_validation_clone("container")
        restore_file_from_commit(container_repo, container_repo, current_head(container_repo), self.metric_path)
        code, out, err = run_eval_container(
            image=self.runtime_image,
            runtime_root=self.runtime_dir,
            run_root=self.run_root,
            worktree=container_repo,
            command=self.benchmark_command,
        )
        if code != 0:
            raise RuntimeError(f"Container benchmark failed:\n{out}\n{err}")
        score, metric_text = self._read_metric_from(container_repo)
        restore_file_from_commit(container_repo, container_repo, current_head(container_repo), self.metric_path)
        code, out, err = run_eval_container(
            image=self.runtime_image,
            runtime_root=self.runtime_dir,
            run_root=self.run_root,
            worktree=container_repo,
            command=self.test_command,
        )
        if code != 0:
            raise RuntimeError(f"Container tests failed:\n{out}\n{err}")
        return {
            "stage": "container",
            "score": score,
            "metric_text": metric_text,
            "worktree": str(container_repo),
            "benchmark_script": None if self.benchmark_script_copy is None else str(self.benchmark_script_copy),
            "test_script": None if self.test_script_copy is None else str(self.test_script_copy),
        }

    async def validate(self) -> dict[str, Any]:
        try:
            self.prepare()
            host = self.validate_host_commands()
            container = self.validate_container_commands()
            report = {
                "status": "ok",
                "run_root": str(self.run_root),
                "docker_image": self.runtime_image,
                "host": host,
                "container": container,
            }
            (self.run_root / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
            return report
        finally:
            self._cleanup_runtime_image()

    async def run(self) -> None:
        try:
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
                valid = self._sorted_valid_candidates(candidates)
                survivors = valid[:survivor_cap]
                for node in candidates:
                    self.state.update_node(node.node_id, surviving=node in survivors)
                if not survivors:
                    self._add_event("round.ended", f"Round {round_index} produced no survivors")
                    frontier = []
                    break
                if round_index < self.args.rounds:
                    self.cleanup_branches(keep_nodes=survivors)
                frontier = survivors
                self._add_event("round.ended", f"Completed round {round_index}", details={"survivors": [n.node_id for n in survivors]})

            final_nodes = [] if self.args.rounds == 0 else [node for node in self.nodes.values() if node.depth == self.args.rounds]
            self.state.set_meta(
                status="completed",
                final_branches=[node.branch_name for node in final_nodes],
                winner_node_id=None if not frontier else frontier[0].node_id,
            )
            summary = {
                "run_id": self.run_id,
                "source_repo": str(self.source_repo),
                "effective_source_repo": str(self.effective_source_repo),
                "source_mode": self.args.source_mode,
                "repo_clone": str(self.repo_clone),
                "run_root": str(self.run_root),
                "final_branches": [node.branch_name for node in final_nodes],
                "winner": None if not frontier else frontier[0].to_dict(),
            }
            (self.run_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            if frontier:
                print(
                    f"[codopt] run completed | winner={frontier[0].node_id} | score={frontier[0].score} | summary={self.run_root / 'summary.json'}",
                    flush=True,
                )
            else:
                print(f"[codopt] run completed with no survivors | summary={self.run_root / 'summary.json'}", flush=True)
        finally:
            self._cleanup_runtime_image()

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
            image=self.runtime_image,
            runtime_root=self.runtime_dir,
            run_root=self.run_root,
            worktree=worktree_path,
            container_name=container_name,
            worker_args=[
                "--project-root",
                str(self.runtime_dir),
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

        result_payload: dict[str, Any] = {}
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
        if node_record.status == "completed":
            self._add_event("node.completed", f"Completed node {node_id}", node_id=node_id, details={"score": node_record.score})
        elif node_record.status == "failed":
            self._add_event(
                "node.failed",
                f"Node {node_id} failed evaluation",
                node_id=node_id,
                details={"error": node_record.error or "", "score": node_record.score},
            )
        return node_record

    def evaluate_baseline(self) -> tuple[float, str]:
        restore_file_from_commit(self.repo_clone, self.repo_clone, current_head(self.repo_clone), self.metric_path)
        code, out, err = run_eval_container(
            image=self.runtime_image,
            runtime_root=self.runtime_dir,
            run_root=self.run_root,
            worktree=self.repo_clone,
            command=self.benchmark_command,
        )
        if code != 0:
            raise RuntimeError(f"Baseline benchmark failed:\n{out}\n{err}")
        score, metric_text = self._read_metric_from(self.repo_clone)
        restore_file_from_commit(self.repo_clone, self.repo_clone, current_head(self.repo_clone), self.metric_path)
        code, out, err = run_eval_container(
            image=self.runtime_image,
            runtime_root=self.runtime_dir,
            run_root=self.run_root,
            worktree=self.repo_clone,
            command=self.test_command,
        )
        if code != 0:
            raise RuntimeError(f"Baseline tests failed:\n{out}\n{err}")
        return score, metric_text

    def parse_metric(self, metric_text: str) -> float:
        stripped = metric_text.strip()
        try:
            if stripped.startswith("{"):
                payload = json.loads(stripped)
                raw_value = float(payload[self.args.metric_key])
            else:
                raw_value = float(stripped)
            return raw_value
        except Exception as exc:
            detail = f"expected JSON key {self.args.metric_key!r}" if stripped.startswith("{") else "expected a plain numeric value"
            raise RuntimeError(f"Unable to parse metric file {self.metric_path}: {detail}. Raw contents: {metric_text!r}") from exc

    def _sorted_valid_candidates(self, candidates: list[NodeRecord]) -> list[NodeRecord]:
        valid = [node for node in candidates if node.score is not None and node.test_passed]
        valid.sort(
            key=lambda node: node.score if node.score is not None else (float("inf") if self.args.lower_is_better else float("-inf")),
            reverse=not self.args.lower_is_better,
        )
        return valid

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
            image=self.runtime_image,
            runtime_root=self.runtime_dir,
            run_root=self.run_root,
            worktree=worktree,
            command=self.benchmark_command,
        )
        if code != 0:
            self.state.update_node(node.node_id, status="failed", finished_at=utc_now(), error=f"benchmark failed\n{stdout}\n{stderr}")
            return self.nodes[node.node_id]

        score, metric_text = self._read_metric_from(worktree)

        restore_file_from_commit(self.repo_clone, worktree, node.trusted_commit, self.metric_path)
        code, stdout, stderr = run_eval_container(
            image=self.runtime_image,
            runtime_root=self.runtime_dir,
            run_root=self.run_root,
            worktree=worktree,
            command=self.test_command,
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

        commit_sha = commit_allowed_changes(worktree, effective_files, f"codopt: candidate {node.node_id}") or node.trusted_commit
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

    def cleanup_branches(self, keep_nodes: list[NodeRecord]) -> None:
        survivor_branches = {node.branch_name for node in keep_nodes}
        survivor_ids = {node.node_id for node in keep_nodes}
        for node in list(self.nodes.values()):
            if node.node_id == "baseline":
                continue
            if node.node_id in survivor_ids:
                self.state.update_node(node.node_id, surviving=True)
                continue
            worktree = Path(node.worktree_path)
            if not self.args.keep_worktrees:
                remove_worktree(self.repo_clone, worktree)
            if node.branch_name not in survivor_branches:
                delete_branch(self.repo_clone, node.branch_name)


async def run_codopt(args: argparse.Namespace, project_root: Path) -> CodoptOrchestrator:
    orchestrator = CodoptOrchestrator(args, project_root)
    await orchestrator.run()
    return orchestrator


async def validate_codopt(args: argparse.Namespace, project_root: Path) -> CodoptOrchestrator:
    orchestrator = CodoptOrchestrator(args, project_root)
    await orchestrator.validate()
    return orchestrator
