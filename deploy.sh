#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$repo_dir/scripts/deploy.py" "$@"
