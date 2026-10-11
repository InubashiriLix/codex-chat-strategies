import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
                prepared = coordinator.prepare()
                self.assertEqual(prepared["capacity"], 5)
                self.assertEqual(len(prepared["lanes"]), 5)
                self.assertEqual(prepared, coordinator.prepare())
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

    def test_dirty_baseline_refused(self):
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
                    parallel.ParallelDeepSeek(Path(tmp) / "data").prepare()


if __name__ == "__main__":
    unittest.main()
