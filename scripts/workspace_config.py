"""Load the pinned worker settings for an explicit task workspace.

Do not call Config.load() and then replace its workspace: loading already
validates the default directory. Do not temporarily change cwd or environment
either; both are process-wide and shared by concurrent worker threads.
"""
from pathlib import Path

from deepseek_mcp import config as worker


def load_workspace_config(workspace: Path, *, expected_workspace_identity=None):
    workspace = Path(workspace)
    if not workspace.is_absolute():
        raise ValueError("workspace must be an absolute directory path")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace must be a directory")
    # Keep this adapter aligned with Config._from_data in the pinned runtime.
    # Its parsers and Config constructor retain credential, setting, identity,
    # protected-directory, and mutation-runtime validation.
    data = worker._load_data()
    base_url = worker._validate_base_url(data.get("base_url", "https://api.deepseek.com"))
    credential = worker._load_api_key(data, base_url)
    flash_model, pro_model = worker._load_model_slots(data)
    flash_effort, pro_effort = worker._load_reasoning_efforts(data)
    return worker.Config(
        api_key=credential,
        workspace=workspace,
        expected_workspace_identity=expected_workspace_identity,
        model=flash_model,
        max_turns=worker._load_max_turns(data),
        max_output_tokens=worker._validate_max_output_tokens(
            data.get("max_output_tokens", worker.DEFAULT_MAX_OUTPUT_TOKENS)),
        max_run_seconds=worker._validate_max_run_seconds(
            data.get("max_run_seconds", worker.DEFAULT_MAX_RUN_SECONDS)),
        allowed_tools=worker._load_allowed_tools(data),
        base_url=base_url,
        flash_model=flash_model,
        pro_model=pro_model,
        reasoning_effort=flash_effort,
        flash_reasoning_effort=flash_effort,
        pro_reasoning_effort=pro_effort,
    )
