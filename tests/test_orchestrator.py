from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from codopt.models import NodeRecord
from codopt.orchestrator import CodoptOrchestrator


class OrchestratorScoringTests(unittest.TestCase):
    def make_orchestrator(self, *, lower_is_better: bool) -> CodoptOrchestrator:
        orchestrator = CodoptOrchestrator.__new__(CodoptOrchestrator)
        orchestrator.args = SimpleNamespace(lower_is_better=lower_is_better, metric_key="score", keep_worktrees=False)
        orchestrator.metric_path = "metric.json"
        return orchestrator

    def test_parse_metric_keeps_raw_value_when_lower_is_better(self) -> None:
        orchestrator = self.make_orchestrator(lower_is_better=True)
        self.assertEqual(orchestrator.parse_metric('{"score": 12.5}'), 12.5)

    def test_sorted_valid_candidates_prefers_smaller_scores_when_lower_is_better(self) -> None:
        orchestrator = self.make_orchestrator(lower_is_better=True)
        nodes = [
            NodeRecord(node_id="a", branch_name="a", parent_id=None, depth=1, worktree_path="/tmp/a", trusted_commit="a", score=10.0, test_passed=True),
            NodeRecord(node_id="b", branch_name="b", parent_id=None, depth=1, worktree_path="/tmp/b", trusted_commit="b", score=3.0, test_passed=True),
            NodeRecord(node_id="c", branch_name="c", parent_id=None, depth=1, worktree_path="/tmp/c", trusted_commit="c", score=7.0, test_passed=True),
        ]
        self.assertEqual([node.node_id for node in orchestrator._sorted_valid_candidates(nodes)], ["b", "c", "a"])

    def test_sorted_valid_candidates_prefers_larger_scores_by_default(self) -> None:
        orchestrator = self.make_orchestrator(lower_is_better=False)
        nodes = [
            NodeRecord(node_id="a", branch_name="a", parent_id=None, depth=1, worktree_path="/tmp/a", trusted_commit="a", score=10.0, test_passed=True),
            NodeRecord(node_id="b", branch_name="b", parent_id=None, depth=1, worktree_path="/tmp/b", trusted_commit="b", score=3.0, test_passed=True),
            NodeRecord(node_id="c", branch_name="c", parent_id=None, depth=1, worktree_path="/tmp/c", trusted_commit="c", score=7.0, test_passed=True),
        ]
        self.assertEqual([node.node_id for node in orchestrator._sorted_valid_candidates(nodes)], ["a", "c", "b"])


class OrchestratorCleanupTests(unittest.TestCase):
    def test_cleanup_branches_prunes_only_nodes_not_kept(self) -> None:
        orchestrator = CodoptOrchestrator.__new__(CodoptOrchestrator)
        orchestrator.args = SimpleNamespace(keep_worktrees=False)
        orchestrator.repo_clone = Path("/tmp/repo")
        kept = NodeRecord(node_id="r2_keep", branch_name="keep", parent_id="baseline", depth=2, worktree_path="/tmp/keep", trusted_commit="keep")
        pruned = NodeRecord(node_id="r2_drop", branch_name="drop", parent_id="baseline", depth=2, worktree_path="/tmp/drop", trusted_commit="drop")
        earlier = NodeRecord(node_id="r1_old", branch_name="old", parent_id="baseline", depth=1, worktree_path="/tmp/old", trusted_commit="old")
        baseline = NodeRecord(node_id="baseline", branch_name="main", parent_id=None, depth=0, worktree_path="/tmp/base", trusted_commit="base")
        updates: list[tuple[str, dict]] = []
        orchestrator.state = SimpleNamespace(update_node=lambda node_id, **changes: updates.append((node_id, changes)))
        orchestrator.nodes = {
            "baseline": baseline,
            kept.node_id: kept,
            pruned.node_id: pruned,
            earlier.node_id: earlier,
        }

        with (
            patch("codopt.orchestrator.remove_worktree") as remove_worktree,
            patch("codopt.orchestrator.delete_branch") as delete_branch,
        ):
            orchestrator.cleanup_branches([kept])

        remove_worktree.assert_any_call(orchestrator.repo_clone, Path(pruned.worktree_path))
        remove_worktree.assert_any_call(orchestrator.repo_clone, Path(earlier.worktree_path))
        delete_branch.assert_any_call(orchestrator.repo_clone, pruned.branch_name)
        delete_branch.assert_any_call(orchestrator.repo_clone, earlier.branch_name)
        self.assertIn((kept.node_id, {"surviving": True}), updates)
        self.assertFalse(any(call.args[1] == Path(kept.worktree_path) for call in remove_worktree.call_args_list))
        self.assertFalse(any(call.args[1] == kept.branch_name for call in delete_branch.call_args_list))


if __name__ == "__main__":
    unittest.main()
