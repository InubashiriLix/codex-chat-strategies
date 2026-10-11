#!/usr/bin/env python3
"""Check native Codex skill discovery without sending a model request."""
import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
import tomllib


def discover_skills(codex_home, cwd):
    env = dict(os.environ, CODEX_HOME=str(codex_home))
    process = subprocess.Popen(
        ['codex', 'app-server', '--stdio'], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = b''

    def request(identifier, method, params):
        nonlocal pending
        payload = {'id': identifier, 'method': method, 'params': params}
        process.stdin.write(json.dumps(payload).encode() + b'\n')
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                if not line.strip():
                    continue
                response = json.loads(line)
                if response.get('id') == identifier:
                    if 'error' in response:
                        raise RuntimeError(response['error'].get('message', 'Codex request failed'))
                    return response['result']
            if not selector.select(timeout=min(1, max(0, deadline-time.monotonic()))):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('Codex app-server exited before discovery completed')
            pending += chunk
        raise RuntimeError('Codex skill discovery timed out')

    try:
        request(1, 'initialize', {
            'clientInfo': {'name': 'strategy-doctor', 'version': '1'},
            'capabilities': {'experimentalApi': True},
        })
        process.stdin.write(b'{"method":"initialized"}\n')
        process.stdin.flush()
        return request(2, 'skills/list', {'cwds': [str(cwd)], 'forceReload': True})['data']
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))))
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'.local/share/codex-strategies')
    parser.add_argument('--cwd', type=Path, default=Path.cwd())
    parser.add_argument('--openai-only', action='store_true')
    args = parser.parse_args()
    codex_home = args.codex_home.expanduser().resolve()
    data_dir = args.data_dir.expanduser().resolve()
    print(f'Codex home: {codex_home}', flush=True)
    if not shutil.which('codex'):
        raise SystemExit('FAIL: codex is not on PATH')
    entries = discover_skills(codex_home, args.cwd.expanduser().resolve())
    modes = ('openai', 'solo') if args.openai_only else ('openai', 'deepseek', 'solo')
    failures = []
    for mode in modes:
        name = f'strategy-{mode}'
        expected = (codex_home/'skills'/name/'SKILL.md').resolve()
        matches = [skill for entry in entries for skill in entry['skills']
                   if skill['name'] == name and Path(skill['path']).resolve() == expected]
        if not matches or not any(skill.get('enabled', True) for skill in matches):
            failures.append(f'{name} is missing or disabled in Codex discovery')
        else:
            print(f'PASS: ${name} loaded by Codex')
        for entry in entries:
            for error in entry['errors']:
                if name in str(error):
                    failures.append(f'{name} has a Codex loading error')
    if not args.openai_only:
        config = tomllib.loads((codex_home/'config.toml').read_text())
        mcp = config.get('mcp_servers', {}).get('strategy_deepseek', {})
        runtime = data_dir/'runtime/bin/python'
        wrapper = data_dir/'chat-mcp.py'
        parallel = data_dir/'parallel_deepseek.py'
        if (not runtime.exists() or not wrapper.exists() or not parallel.exists() or mcp.get('enabled', True) is False
                or mcp.get('command') != str(runtime) or mcp.get('args') != [str(wrapper)]):
            failures.append('DeepSeek runtime or MCP registration is missing/mismatched')
        else:
            print('PASS: DeepSeek runtime and MCP registration present')
        private = Path.home()/'.deepseek-mcp/config.json'
        settings = json.loads(private.read_text()) if private.exists() else {}
        if os.environ.get('DEEPSEEK_API_KEY') or settings.get('api_key'):
            print('DeepSeek key: present (API access not tested)')
        else:
            print('DeepSeek: NOT READY — API key missing; OpenAI/solo remain usable')
            print(f'Setup: python3 "{data_dir}/setup-key.py"')
            failures.append('DeepSeek API key missing')
    print('In Codex: /skills → List skills, or type $strategy-openai / $strategy-solo.')
    print('Legacy /prompts:* files do not prove command support in your CLI.')
    print('No model requests sent; model access and delegation are not tested.')
    for failure in failures:
        print(f'FAIL: {failure}')
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f'FAIL: {exc}') from exc
