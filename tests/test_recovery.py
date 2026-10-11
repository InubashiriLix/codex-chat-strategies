from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import deepseek_recovery as recovery
    import deepseek_diagnostics as diagnostics
    from deepseek_mcp import agent_loop
    from deepseek_mcp.config import Config
    from deepseek_mcp.job_manager import DeepSeekJobManager
except ModuleNotFoundError:
    recovery = None


@unittest.skipIf(recovery is None, "installed DeepSeek worker runtime required")
class RecoveryTests(unittest.TestCase):
    def test_budget_failure_checkpoint_and_one_shot_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "repo"
            workspace.mkdir()
            subprocess.run(["git", "-C", str(workspace), "init"], check=True, capture_output=True)
            (workspace / "changed.txt").write_text("retained")
            config = Config("sk-test-placeholder", workspace, allowed_tools=["Read"])
            original_start, original_finish = DeepSeekJobManager.start, DeepSeekJobManager._finish_job
            try:
                with patch.object(recovery, "load_recovery_config", return_value=config), patch.object(
                    Config, "load", return_value=config
                ), patch.object(agent_loop, "MAX_TOTAL_TOKENS_PER_RUN", 1), patch(
                    "deepseek_mcp.execution_lock.DEFAULT_LOCK_DIRECTORY", root / "locks"
                ), patch("deepseek_mcp.transaction_journal.JOURNAL_DIRECTORY", root / "journal"):
                    diagnostics.install_job_diagnostics(root / "data/diagnostics")
                    service = recovery.Recovery(root / "data")
                    manager = DeepSeekJobManager()
                    job_id = manager.start("original bounded goal", "short context", config)["job_id"]
                    self.assertTrue(manager.wait_for_terminal(job_id, timeout=5))
                    deadline = time.monotonic() + 2
                    while not diagnostics.read_diagnostic(root / "data/diagnostics", job_id)["found"]:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.02)
                    packet = service.packet(job_id)
                    self.assertEqual(packet["diagnostic"]["kind"], "local_token_budget")
                    self.assertTrue(packet["retry_available"])
                    self.assertEqual(packet["original_task"], "original bounded goal")
                    self.assertIn("?? changed.txt", packet["git_status"]["changed_paths"])
                    self.assertNotIn("sk-test-placeholder", str(packet))
                    with self.assertRaisesRegex(ValueError, "state changed"):
                        service.start(job_id, "finish only missing part", "stale")
                    resumed = service.start(job_id, "finish only missing part", packet["state_token"])
                    self.assertEqual(resumed["parent_job_id"], job_id)
                    child_id = resumed["job_id"]
                    self.assertTrue(service.managers[child_id].wait_for_terminal(child_id, timeout=5))
                    self.assertEqual(service.status(child_id)["status"], "failed")
                    self.assertEqual(recovery._read(service.checkpoints, child_id)["depth"], 1)
                    with self.assertRaisesRegex(ValueError, "blocked"):
                        service.start(job_id, "try again", packet["state_token"])
                    deadline = time.monotonic() + 2
                    while not diagnostics.read_diagnostic(root / "data/diagnostics", child_id)["found"]:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.02)
                    self.assertFalse(service.packet(child_id)["retry_available"])
                    with self.assertRaisesRegex(ValueError, "blocked"):
                        service.start(child_id, "third try", service.packet(child_id)["state_token"])
            finally:
                DeepSeekJobManager.start = original_start
                DeepSeekJobManager._finish_job = original_finish

    def test_unsafe_failure_and_pending_mutation_block_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            config = Config("sk-test-placeholder", workspace)
            original_start = DeepSeekJobManager.start
            service = recovery.Recovery(root / "data")
            job_id = "123456789abc"
            record = {"version": 1, "job_id": job_id, "workspace": str(workspace.resolve()),
                      "base_workspace": str(workspace.resolve()), "workspace_identity": config.expected_workspace_identity,
                      "task": "x", "task_truncated": False, "context": "", "context_truncated": False,
                      "model": config.model, "capability": "coding", "depth": 0}
            recovery._save(service.checkpoints, job_id, record)
            try:
                with patch.object(recovery, "load_recovery_config", return_value=config), patch.object(
                    recovery, "query_with_lease", return_value=[]
                ):
                    diagnostics.write_diagnostic(service.diagnostics, job_id, "failed", "status=402")
                    self.assertFalse(service.packet(job_id)["retry_available"])
                    diagnostics.write_diagnostic(service.diagnostics, job_id, "failed", "run token budget")
                    with patch.object(recovery, "query_with_lease", return_value=[{"transaction_id": "pending"}]):
                        self.assertFalse(service.packet(job_id)["retry_available"])
                    workspace.rename(root / "moved")
                    with self.assertRaises(Exception):
                        service.packet(job_id)
            finally:
                DeepSeekJobManager.start = original_start


if __name__ == "__main__":
    unittest.main()
