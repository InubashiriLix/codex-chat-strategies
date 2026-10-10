"""Run with the isolated worker Python, which provides tomlkit."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tomlkit

codex, data = (Path(arg) for arg in sys.argv[1:])
codex.mkdir(parents=True, exist_ok=True)
config = codex/'config.toml'
if config.is_symlink():
    config = config.resolve()
original = config.read_text() if config.exists() else ''
doc = tomlkit.parse(original)
servers = doc.setdefault('mcp_servers', tomlkit.table())
existing = servers.get('strategy_deepseek')
command = str(data/'runtime/bin/python')
entry = str(data/'chat-mcp.py')
if existing is not None:
    expected = existing.get('command') == command and list(existing.get('args', [])) == [entry]
    if not expected:
        raise SystemExit('strategy_deepseek already belongs to another installation; refusing to overwrite.')
else:
    servers['strategy_deepseek'] = {
        'command': command, 'args': [entry], 'enabled': True,
        'startup_timeout_sec': 30, 'tool_timeout_sec': 960,
        'default_tools_approval_mode': 'writes',
        'env_vars': ['DEEPSEEK_API_KEY', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                     'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR'],
        'tools': {tool: {'approval_mode': 'approve'} for tool in
                  ('get_deepseek_result', 'get_deepseek_recovery', 'acknowledge_deepseek_mutations')},
    }
updated = tomlkit.dumps(doc)
if updated != original:
    backups = data/'backups'
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    if config.exists():
        backup = backups/(str(time.time_ns())+'-config.toml')
        shutil.copy2(config, backup)
        backup.chmod(0o600)
    fd, tmp = tempfile.mkstemp(dir=config.parent, prefix='.strategy-config-')
    with os.fdopen(fd, 'w') as f:
        f.write(updated)
    os.replace(tmp, config)
# Never overwrite an existing API key or provider choice.
private = Path.home()/'.deepseek-mcp'
private.mkdir(mode=0o700, exist_ok=True)
target = private/'config.json'
if not target.exists():
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'api_key': '', 'flash': 'deepseek-flash', 'pro': 'deepseek-v4-pro',
                   'flash_reasoning_effort': 'high', 'pro_reasoning_effort': 'high',
                   'max_turns': 20, 'max_run_seconds': 900,
                   'allowed_tools': ['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep', 'NotebookEdit']}, f, indent=2)
print('DeepSeek MCP registered; existing account settings and credentials preserved.')
