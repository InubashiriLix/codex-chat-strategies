import importlib.util
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_offline_install_idempotency_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex, data = Path(tmp)/'codex with spaces', Path(tmp)/'runtime'
            codex.mkdir()
            original = '# preserve me\nmodel = "chosen-model"\n'
            (codex/'config.toml').write_text(original)
            command = [str(ROOT/'deploy.sh'), '--openai-only', '--codex-home', str(codex), '--data-dir', str(data)]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertIn('Installed skills: $strategy-openai, $strategy-solo', result.stdout)
            self.assertIn('/skills', result.stdout)
            self.assertTrue((data/'doctor.py').exists())
            prompt = codex/'prompts/openai.md'
            self.assertIn(str(codex/'skills/strategy-openai/SKILL.md'), prompt.read_text())
            self.assertIn('$ARGUMENTS', prompt.read_text())
            self.assertNotIn('{{', prompt.read_text())
            self.assertFalse((codex/'prompts/deepseek.md').exists())
            subprocess.run(command, check=True, capture_output=True)
            self.assertFalse((data/'backups').exists())
            prompt.write_text('local edit')
            subprocess.run(command, check=True, capture_output=True)
            self.assertTrue(any(p.read_text() == 'local edit' for p in (data/'backups').iterdir()))
            self.assertEqual((codex/'config.toml').read_text(), original)

    @unittest.skipUnless(importlib.util.find_spec('tomlkit'), 'tomlkit is available in the installed worker runtime')
    def test_mcp_registration_preserves_credentials_and_user_config(self):
        import json
        import tomlkit
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp); codex = home/'codex'; data = home/'data'; codex.mkdir(); data.mkdir()
            config = codex/'config.toml'
            config.write_text('# existing comment\nmodel = "chosen"\n[mcp_servers.other]\ncommand = "other-tool"\n')
            private = home/'.deepseek-mcp';private.mkdir(mode=0o700)
            credentials = private/'config.json';credentials.write_text(json.dumps({'api_key': 'test-only-placeholder', 'max_turns': 3}))
            before = credentials.read_bytes()
            argv = ['register-mcp.py', str(codex), str(data)]
            for _ in range(2):
                with patch.object(Path, 'home', return_value=home), patch.object(sys, 'argv', argv):
                    runpy.run_path(str(ROOT/'scripts/register-mcp.py'), run_name='__main__')
            doc = tomlkit.parse(config.read_text())
            self.assertEqual(doc['model'], 'chosen')
            self.assertEqual(doc['mcp_servers']['other']['command'], 'other-tool')
            self.assertIn('# existing comment', config.read_text())
            self.assertEqual(credentials.read_bytes(), before)
            self.assertEqual(len(list((data/'backups').iterdir())), 1)
            self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
