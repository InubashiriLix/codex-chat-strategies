import importlib.util
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import parallel_deepseek as parallel
    from deepseek_mcp.config import Config
    from deepseek_mcp.execution_lock import acquire_workspace_lease, WorkspaceLockBusy
except ModuleNotFoundError:
    parallel = None


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipIf(parallel is None, "installed DeepSeek worker runtime required")
class ParallelTests(unittest.TestCase):
    def test_five_independent_lanes_and_clean_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.name", "Test")
            git(repo, "config", "user.email", "test@example.invalid")
            (repo / "readme.txt").write_text("baseline\n")
            git(repo, "add", "readme.txt")
            git(repo, "commit", "-m", "baseline")
            coordinator = parallel.ParallelDeepSeek(base / "data")
            config = Config(api_key="sk-test-placeholder", workspace=repo)
            with patch.object(parallel, "load_recovery_config", return_value=config), patch(
                "deepseek_mcp.execution_lock.DEFAULT_LOCK_DIRECTORY", base / "locks"
            ), patch(
                "deepseek_mcp.transaction_journal.JOURNAL_DIRECTORY", base / "journal"
            ):
                prepared = coordinator.prepare(mode="worktree")
                self.assertEqual(prepared["capacity"], 5)
                self.assertEqual(len(prepared["lanes"]), 5)
                self.assertEqual(prepared, coordinator.prepare(mode="worktree"))
                leases = []
                try:
                    for slot in coordinator.slots.values():
                        self.assertEqual((slot.path / "readme.txt").read_text(), "baseline\n")
                        leases.append(acquire_workspace_lease(slot.path, base / "locks"))
                    with self.assertRaises(WorkspaceLockBusy):
                        acquire_workspace_lease(coordinator.slots[1].path, base / "locks")
                finally:
                    for lease in leases:
                        lease.release()
                self.assertEqual(len(coordinator.inspect(prepared["batch_id"])["lanes"]), 5)
                untracked = coordinator.slots[1].path / "unintegrated.txt"
                untracked.write_text("keep me\n")
                with self.assertRaisesRegex(ValueError, "unintegrated changes"):
                    coordinator.close(prepared["batch_id"])
                untracked.unlink()
                self.assertTrue(coordinator.close(prepared["batch_id"])["closed"])
                self.assertFalse(coordinator.slots)

    def test_worktree_dirty_baseline_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.name", "Test")
            git(repo, "config", "user.email", "test@example.invalid")
            (repo / "tracked.txt").write_text("baseline\n")
            git(repo, "add", "tracked.txt")
            git(repo, "commit", "-m", "baseline")
            (repo / "tracked.txt").write_text("dirty\n")
            config = Config(api_key="sk-test-placeholder", workspace=repo)
            with patch.object(parallel, "load_recovery_config", return_value=config):
                with self.assertRaisesRegex(ValueError, "working-tree changes"):
                    parallel.ParallelDeepSeek(Path(tmp) / "data").prepare(mode="worktree")


    def test_direct_dirty_multi_repo_and_non_git_preserved_on_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = [base / "frontend", base / "backend", base / "docs"]
            for path in paths:
                path.mkdir()
                (path / "existing.txt").write_text("local edits")
            for path in paths[:2]:
                git(path, "init")  # No committed HEAD is needed either.
            coordinator = parallel.ParallelDeepSeek(base / "data")
            with patch.object(parallel, "load_recovery_config", side_effect=AssertionError("default workspace must not be needed")), patch(
                "deepseek_mcp.execution_lock.DEFAULT_LOCK_DIRECTORY", base / "locks"
            ), patch("deepseek_mcp.transaction_journal.JOURNAL_DIRECTORY", base / "journal"):
                prepared = coordinator.prepare(workspaces=[str(p) for p in paths])
                self.assertEqual(prepared["mode"], "direct")
                self.assertIsNone(prepared["base_head"])
                self.assertIsNone(coordinator.inspect(prepared["batch_id"])["lanes"]["3"]["status"])
                with patch.object(parallel, "query_with_lease", return_value=[{"transaction_id": "pending"}]):
                    with self.assertRaisesRegex(ValueError, "unacknowledged"):
                        coordinator.close(prepared["batch_id"])
                self.assertTrue(coordinator.close(prepared["batch_id"])["closed"])
            for path in paths:
                self.assertEqual((path / "existing.txt").read_text(), "local edits")

    def test_auto_start_five_workspaces_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            default = base / "default"
            default.mkdir()
            paths = [base / f"repo-{n}" for n in range(6)]
            for path in paths:
                path.mkdir()
            config = Config(api_key="sk-test-placeholder", workspace=default)
            coordinator = parallel.ParallelDeepSeek(base / "data")
            managers = []
            def manager():
                instance = MagicMock()
                instance.start.side_effect = lambda task, context, cfg: {"job_id": f"job-{instance.start.call_count}-{len(managers)}"}
                instance.result_with_usage_claim.return_value = ({"ready": True}, None)
                managers.append(instance)
                return instance
            with patch.object(parallel, "DeepSeekJobManager", side_effect=manager), patch.object(parallel, "load_workspace_config", side_effect=lambda workspace, **kw: replace(config, workspace=workspace, **kw)), patch.object(
                parallel, "load_recovery_config", side_effect=AssertionError("explicit paths do not require ping/default workspace")
            ):
                jobs = [coordinator.start(task="bounded task", workspace=str(path), readonly=(n == 0)) for n, path in enumerate(paths[:5])]
                self.assertEqual([job["lane"] for job in jobs], [1, 2, 3, 4, 5])
                self.assertEqual(len({job["batch_id"] for job in jobs}), 1)
                for n, instance in enumerate(managers):
                    delegated = instance.start.call_args.args[2]
                    self.assertEqual(delegated.workspace, paths[n])
                    self.assertEqual(delegated.delegation_capability, "readonly" if n == 0 else "coding")
                with self.assertRaisesRegex(ValueError, "All lanes"):
                    coordinator.start(task="sixth", workspace=str(paths[5]))
                with self.assertRaisesRegex(ValueError, "Collect every"):
                    coordinator.close(jobs[0]["batch_id"])
                coordinator.result(jobs[0]["job_id"])
                reused = coordinator.start(task="follow-up", workspace=str(paths[0]))
                self.assertEqual(reused["lane"], 1)

    def test_overlap_aliases_and_invalid_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            child = repo / "module"
            child.mkdir()
            alias = base / "alias"
            alias.symlink_to(repo, target_is_directory=True)
            coordinator = parallel.ParallelDeepSeek(base / "data")
            for other in (repo, child, alias):
                with self.assertRaisesRegex(ValueError, "non-overlapping"):
                    coordinator.prepare(workspaces=[str(repo), str(other)])
                self.assertIsNone(coordinator.batch_id)
            with self.assertRaisesRegex(ValueError, "absolute"):
                coordinator.start(task="task", workspace="relative")
            with self.assertRaisesRegex(ValueError, "empty"):
                coordinator.start(task="", workspace=str(repo))
            config = Config(api_key="sk-test-placeholder", workspace=repo)
            with patch.object(parallel, "load_workspace_config", side_effect=lambda workspace, **kw: replace(config, workspace=workspace, **kw)), patch.object(parallel.DeepSeekJobManager, "start", return_value={"job_id": "first"}):
                first = coordinator.start(task="task", workspace=str(repo))
                for other in (repo, child, alias):
                    with self.assertRaisesRegex(ValueError, "overlapping"):
                        coordinator.start(task="task", workspace=str(other))
                self.assertEqual(len(coordinator.slots), 1)  # Failed starts do not consume capacity.
                with self.assertRaisesRegex(ValueError, "Unknown parallel batch"):
                    coordinator.start(batch_id="stale", task="task", workspace=str(repo))
                self.assertEqual(first["workspace"], str(repo))

    def test_registered_tools_auto_start_and_schema(self):
        import asyncio
        from mcp.server.fastmcp import FastMCP
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            mcp = FastMCP("test")
            parallel.register_parallel_tools(mcp, root / "data")
            schemas = {tool.name: tool.inputSchema for tool in asyncio.run(mcp.list_tools())}
            schema = schemas["start_deepseek_parallel"]
            self.assertFalse(schema.get("required"))
            self.assertIn("workspace", schema["properties"])
            self.assertEqual(schema["properties"]["lane"]["default"], 0)
            self.assertEqual(schemas["prepare_deepseek_parallel"]["properties"]["mode"]["default"], "direct")

    def test_real_worker_explicit_workspace_and_recovery_without_provider_call(self):
        from deepseek_mcp import agent_loop
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            default = base / "default"
            target = base / "target"
            default.mkdir()
            target.mkdir()
            config = Config(api_key="sk-test-placeholder", workspace=default)
            coordinator = parallel.ParallelDeepSeek(base / "data")
            with patch.object(parallel, "load_workspace_config", side_effect=lambda workspace, **kw: replace(config, workspace=workspace, **kw)), patch.object(agent_loop, "MAX_TOTAL_TOKENS_PER_RUN", 1), patch(
                "deepseek_mcp.execution_lock.DEFAULT_LOCK_DIRECTORY", base / "locks"
            ), patch("deepseek_mcp.transaction_journal.JOURNAL_DIRECTORY", base / "journal"), patch(
                "deepseek_mcp.server._record_usage", return_value=True
            ):
                job = coordinator.start(task="read target", workspace=str(target), readonly=True)
                manager = coordinator.slots[job["lane"]].manager
                self.assertTrue(manager.wait_for_terminal(job["job_id"], timeout=5))
                self.assertTrue(coordinator.result(job["job_id"])["ready"])
                self.assertEqual(coordinator.recovery(job["batch_id"], job["lane"])["count"], 0)
                self.assertTrue(coordinator.close(job["batch_id"])["closed"])
                self.assertTrue(target.is_dir())


if __name__ == "__main__":
    unittest.main()
