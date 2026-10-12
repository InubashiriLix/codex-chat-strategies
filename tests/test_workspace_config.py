from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from deepseek_mcp import config as worker
    from workspace_config import load_workspace_config
    import parallel_deepseek as parallel
except ModuleNotFoundError:
    worker = None


@unittest.skipIf(worker is None, "installed DeepSeek worker runtime required")
class WorkspaceConfigTests(unittest.TestCase):
    def test_explicit_lanes_ignore_invalid_defaults_without_global_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / str(n) for n in range(5)]
            for path in paths:
                path.mkdir()
            coordinator = parallel.ParallelDeepSeek(root / "data")
            original_cwd = Path.cwd()
            with patch.object(worker, "_load_data", return_value={"workspace": "/missing/default"}), patch.object(
                worker, "_load_api_key", return_value="sk-test-placeholder"
            ), patch.dict(os.environ, {"DEEPSEEK_WORKSPACE": str(Path.home())}), patch.object(
                parallel.DeepSeekJobManager, "start",
                side_effect=lambda task, context, config: {"job_id": config.workspace.name}
            ):
                with self.assertRaisesRegex(RuntimeError, "Workspace must"):
                    worker.Config.load()
                with ThreadPoolExecutor(max_workers=5) as pool:
                    jobs = list(pool.map(lambda path: coordinator.start(task="test", workspace=str(path)), paths))
                self.assertEqual({job["lane"] for job in jobs}, {1, 2, 3, 4, 5})
                self.assertEqual({job["workspace"] for job in jobs}, {str(p) for p in paths})
                self.assertEqual(len({job["batch_id"] for job in jobs}), 1)
                self.assertEqual(os.environ["DEEPSEEK_WORKSPACE"], str(Path.home()))
                self.assertEqual(Path.cwd(), original_cwd)

    def test_adapter_preserves_all_pinned_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            data = {"workspace": tmp, "max_turns": 12, "max_run_seconds": 120,
                    "max_output_tokens": 4096, "allowed_tools": ["Read", "Glob"]}
            with patch.object(worker, "_load_data", return_value=data), patch.object(
                worker, "_load_api_key", return_value="sk-test-placeholder"
            ), patch.dict(os.environ, {"DEEPSEEK_WORKSPACE": tmp}):
                self.assertEqual(asdict(load_workspace_config(workspace)), asdict(worker.Config.load()))

    def test_explicit_workspace_safety_identity_and_settings_still_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            workspace.mkdir()
            with patch.object(worker, "_load_data", return_value={}), patch.object(
                worker, "_load_api_key", return_value="sk-test-placeholder"
            ):
                for unsafe in (Path.home(), Path("/")):
                    with self.subTest(unsafe=unsafe), self.assertRaisesRegex(RuntimeError, "protected|broad"):
                        load_workspace_config(unsafe)
                with self.assertRaisesRegex(ValueError, "absolute"):
                    load_workspace_config(Path("relative"))
                identity = load_workspace_config(workspace).expected_workspace_identity
                workspace.rename(workspace.with_name("old"))
                workspace.mkdir()
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    load_workspace_config(workspace, expected_workspace_identity=identity)
                with patch.object(worker, "_load_data", return_value={"max_turns": 0}):
                    with self.assertRaisesRegex(RuntimeError, "max_turns"):
                        load_workspace_config(workspace)


if __name__ == "__main__":
    unittest.main()
