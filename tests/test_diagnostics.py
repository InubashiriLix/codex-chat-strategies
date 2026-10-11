from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import deepseek_diagnostics as diagnostics
    from deepseek_mcp import agent_loop
    from deepseek_mcp.config import Config
    from deepseek_mcp.job_manager import DeepSeekJobManager
except ModuleNotFoundError:
    diagnostics = None


@unittest.skipIf(diagnostics is None, "installed DeepSeek worker runtime required")
class DiagnosticTests(unittest.TestCase):
    def test_budget_failure_persisted_without_original_error_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            original = DeepSeekJobManager._finish_job
            try:
                diagnostics.install_job_diagnostics(root / "diagnostics")
                config = Config("sk-test-placeholder", workspace, allowed_tools=["Read"])
                with patch.object(agent_loop, "MAX_TOTAL_TOKENS_PER_RUN", 1), patch(
                    "deepseek_mcp.execution_lock.DEFAULT_LOCK_DIRECTORY", root / "locks"
                ), patch(
                    "deepseek_mcp.transaction_journal.JOURNAL_DIRECTORY", root / "journal"
                ):
                    manager = DeepSeekJobManager()
                    job_id = manager.start("small test", "", config)["job_id"]
                    self.assertTrue(manager.wait_for_terminal(job_id, timeout=5))
                    self.assertEqual(manager.status(job_id)["status"], "failed")
                    deadline = time.monotonic() + 2
                    while True:
                        record = diagnostics.read_diagnostic(root / "diagnostics", job_id)
                        if record["found"] or time.monotonic() >= deadline:
                            break
                        time.sleep(0.02)
                self.assertEqual(record["kind"], "local_token_budget")
                self.assertEqual(record["source"], "automatic")
                self.assertNotIn("sk-test-placeholder", str(record))
            finally:
                DeepSeekJobManager._finish_job = original

    def test_provider_codes_distinguished(self):
        self.assertEqual(diagnostics.classify_error("category=api status=402")["kind"], "provider_balance")
        self.assertEqual(diagnostics.classify_error("category=api status=401")["kind"], "provider_auth")
        self.assertEqual(diagnostics.classify_error("category=rate_limit status=429")["kind"], "provider_rate_limit")


if __name__ == "__main__":
    unittest.main()
