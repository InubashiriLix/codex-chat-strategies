#!/usr/bin/env python3
"""Store a DeepSeek key locally without echoing or accepting it in shell arguments."""
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile

if len(sys.argv) != 1 or not sys.stdin.isatty():
    raise SystemExit('Run interactively without arguments; do not put the key in command history.')
path = Path.home()/'.deepseek-mcp/config.json'
if not path.exists():
    raise SystemExit('Run deploy.sh first.')
key = getpass.getpass('DeepSeek API key（隐藏输入）: ').strip()
if not key:
    raise SystemExit('Empty input; unchanged.')
data = json.loads(path.read_text())
data['api_key'] = key
fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.key-')
with os.fdopen(fd, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
os.chmod(tmp, 0o600)
os.replace(tmp, path)
print('Key saved locally. No API request was sent.')
