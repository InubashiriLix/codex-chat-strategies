#!/usr/bin/env python3
"""Install in-chat skills and legacy prompt compatibility files."""
import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib

REPO = Path(__file__).resolve().parents[1]
UPSTREAM = 'https://github.com/PsChina/deepseek-as-subagent.git'
REVISION = '7e54026392f85ccfc796f947faf9ffac3968a49e'
MODES = ('openai', 'deepseek', 'solo')


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def write_file(path, content, backups, executable=False):
    path = path.resolve() if path.is_symlink() else path
    if path.exists() and path.read_text() == content:
        return
    if path.exists():
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        saved = backups / (str(time.time_ns()) + '-' + path.name)
        shutil.copy2(path, saved)
        saved.chmod(0o600)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.strategy-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.chmod(tmp, 0o755 if executable else 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install_backend(data):
    runtime = data / 'runtime'
    marker = data / 'upstream-commit.txt'
    python = runtime / 'bin/python'
    if python.exists() and marker.exists() and marker.read_text().strip() == REVISION:
        run(python, '-c', 'import deepseek_mcp, tomlkit')
        return python
    if runtime.exists():
        raise RuntimeError(f'Existing unrecognized runtime: {runtime}; choose a new --data-dir.')
    for command in ('git', 'uv'):
        if not shutil.which(command):
            raise RuntimeError(f'{command} is required for DeepSeek; install it or use --openai-only.')
    # The worker runtime must be outside repositories it will modify.
    source = data / ('upstream-' + REVISION[:12])
    if not source.exists():
        with tempfile.TemporaryDirectory(prefix='.download-', dir=data) as tmp:
            checkout = Path(tmp) / 'source'
            run('git', 'init', checkout)
            run('git', '-C', checkout, 'fetch', '--depth=1', UPSTREAM, REVISION)
            run('git', '-C', checkout, 'checkout', '--detach', 'FETCH_HEAD')
            if subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip() != REVISION:
                raise RuntimeError('Upstream revision mismatch')
            shutil.move(str(checkout), source)
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != REVISION:
        raise RuntimeError('Cached upstream revision mismatch')
    run('uv', 'venv', '--python', '3.12', runtime)
    run('uv', 'pip', 'install', '--python', python, '--require-hashes', '--only-binary', ':all:', '-r', source/'requirements.lock')
    run('uv', 'pip', 'install', '--python', python, '--no-deps', '--no-build-isolation', source)
    run('uv', 'pip', 'check', '--python', python)
    marker.write_text(REVISION + '\n')
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openai-only', action='store_true', help='Install OpenAI/solo commands without DeepSeek downloads or MCP changes.')
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))))
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'.local/share/codex-strategies')
    args = parser.parse_args()
    codex = args.codex_home.expanduser().resolve()
    data = args.data_dir.expanduser().resolve()
    if data.is_relative_to(REPO):
        parser.error('--data-dir must be outside this repository (runtime and local backups).')
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    backups = data / 'backups'
    modes = ('openai', 'solo') if args.openai_only else MODES
    if not args.openai_only:
        python = install_backend(data)
        for name in ('chat-mcp.py', 'deepseek_diagnostics.py', 'parallel_deepseek.py', 'setup-key.py'):
            write_file(data/name, (REPO/'scripts'/name).read_text(), backups)
        # Register before exposing the command. A failure leaves existing commands untouched.
        run(python, REPO/'scripts/register-mcp.py', codex, data)
    for mode in modes:
        skill = codex/'skills'/f'strategy-{mode}'
        for source in sorted((REPO/'skills'/f'strategy-{mode}').rglob('*')):
            if source.is_file():
                content = source.read_text().replace('{{DATA_DIR}}', str(data))
                write_file(skill/source.relative_to(REPO/'skills'/f'strategy-{mode}'), content, backups)
        prompt = (REPO/'prompts'/f'{mode}.md').read_text().replace('{{SKILL_PATH}}', str(skill/'SKILL.md'))
        write_file(codex/'prompts'/f'{mode}.md', prompt, backups)
    write_file(data/'doctor.py', (REPO/'scripts/doctor.py').read_text(), backups)
    print('Installed skills: ' + ', '.join('$strategy-' + mode for mode in modes))
    print('In Codex, use /skills → List skills, or type the skill name above.')
    print('Legacy /prompts:* files are optional compatibility files; some CLIs do not support them.')
    print('If skills do not appear, restart Codex. Use /model to choose your main model.')
    if not args.openai_only:
        print(f'DeepSeek key setup (hidden local input): python3 "{data}/setup-key.py"')
    print(f'Updated files are backed up locally under {backups}; no model requests were sent.')
    check = ['python3', str(data/'doctor.py'), '--codex-home', str(codex), '--data-dir', str(data)]
    if args.openai_only:
        check.append('--openai-only')
    print('Validate native discovery: ' + shlex.join(check))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
