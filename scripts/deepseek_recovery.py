"""Bounded, private checkpoints and one-shot recovery for failed jobs."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

from deepseek_mcp.config import Config
from deepseek_mcp.execution_profile import CODING_PROFILE, READONLY_PROFILE, configure_delegation
from deepseek_mcp.job_manager import DeepSeekJobManager, JobError
from deepseek_mcp.transaction_recovery import load_recovery_config, query_with_lease
from deepseek_mcp.workspace_guard import require_workspace_identity

from deepseek_diagnostics import JOB_ID, read_diagnostic

MAX_TASK = 8000
MAX_CONTEXT = 2000
MAX_FILE = 16000
RECOVERABLE = {"local_token_budget", "local_history_budget", "local_turn_limit"}


def _save(directory: Path, job_id: str, record: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    if len(payload) > MAX_FILE:
        raise ValueError("Checkpoint is too large")
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, directory / f"{job_id}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(directory: Path, job_id: str) -> dict | None:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid DeepSeek job ID")
    try:
        fd = os.open(directory / f"{job_id}.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("Checkpoint is not a private regular file")
        payload = os.read(fd, MAX_FILE + 1)
    finally:
        os.close(fd)
    if len(payload) > MAX_FILE:
        raise ValueError("Checkpoint is too large")
    value = json.loads(payload)
    if (value.get("job_id") != job_id or value.get("version") != 1
            or not isinstance(value.get("workspace"), str)
            or not isinstance(value.get("workspace_identity"), str)
            or value.get("depth") not in (0, 1)):
        raise ValueError("Invalid checkpoint")
    return value


def install_checkpoints(directory: Path) -> None:
    original = DeepSeekJobManager.start
    if getattr(original, "_strategy_checkpoint", False):
        return

    def start(self, task, context, config):
        snapshot = original(self, task, context, config)
        try:
            base = load_recovery_config().workspace.resolve()
            parent = getattr(self, "_strategy_recovery_parent", None)
            _save(directory, snapshot["job_id"], {
                "version": 1, "job_id": snapshot["job_id"], "created_at": time.time(),
                "workspace": str(config.workspace.resolve()), "base_workspace": str(base),
                "workspace_identity": config.expected_workspace_identity,
                "task": task[:MAX_TASK], "task_truncated": len(task) > MAX_TASK,
                "context": context[:MAX_CONTEXT], "context_truncated": len(context) > MAX_CONTEXT,
                "model": config.model, "capability": config.delegation_capability,
                "depth": 1 if parent else 0, "parent_job_id": parent,
            })
        except Exception:
            # Checkpoint failure must not change an already started provider job.
            pass
        return snapshot

    start._strategy_checkpoint = True
    DeepSeekJobManager.start = start


def _git_status(workspace: Path) -> dict:
    command = ["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    if result.returncode:
        return {"available": False}
    lines = result.stdout.splitlines()
    return {"available": True, "changed_paths": [line[:300] for line in lines[:80]],
            "truncated": len(lines) > 80 or any(len(line) > 300 for line in lines[:80])}


class Recovery:
    def __init__(self, data_dir: Path):
        self.checkpoints = data_dir / "checkpoints"
        self.diagnostics = data_dir / "diagnostics"
        self.managers: dict[str, DeepSeekJobManager] = {}
        install_checkpoints(self.checkpoints)

    def _validated(self, job_id: str) -> tuple[dict, Path]:
        record = _read(self.checkpoints, job_id)
        if record is None:
            raise ValueError("No checkpoint for this job (older jobs cannot be reconstructed automatically)")
        base = load_recovery_config().workspace.resolve()
        if str(base) != record.get("base_workspace"):
            raise ValueError("Codex base workspace differs from the checkpoint")
        workspace = Path(record["workspace"])
        require_workspace_identity(workspace, record["workspace_identity"])
        return record, workspace

    def packet(self, job_id: str) -> dict:
        record, workspace = self._validated(job_id)
        diagnostic = read_diagnostic(self.diagnostics, job_id)
        if not diagnostic["found"]:
            raise ValueError("Job has no saved terminal failure; wait for it to finish")
        recovery_config = replace(load_recovery_config(), workspace=workspace,
                                  expected_workspace_identity=record["workspace_identity"])
        try:
            pending = query_with_lease(recovery_config)
            busy = False
        except Exception as error:
            if "busy" not in str(error).lower():
                raise
            pending, busy = [], True
        git_status = _git_status(workspace)
        state_token = hashlib.sha256(json.dumps({
            "job_id": job_id, "identity": record["workspace_identity"],
            "git_status": git_status, "pending": pending, "busy": busy,
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        return {
            "job_id": job_id, "diagnostic": diagnostic, "workspace": str(workspace),
            "original_task": record["task"], "task_truncated": record["task_truncated"],
            "original_context": record["context"], "context_truncated": record["context_truncated"],
            "git_status": git_status, "pending_transactions": pending,
            "workspace_busy": busy, "recovery_depth": record["depth"],
            "state_token": state_token,
            "retry_available": diagnostic["kind"] in RECOVERABLE and record["depth"] == 0
                               and not busy and not pending and not self._claimed(job_id),
            "instruction": "Review changed files and exact pending transactions before retrying. Supply only the unfinished work in remaining_task; do not repeat completed edits.",
        }

    def _claimed(self, job_id: str) -> bool:
        return (self.checkpoints / f"{job_id}.claimed").exists()

    def start(self, job_id: str, remaining_task: str, state_token: str) -> dict:
        if not remaining_task.strip() or len(remaining_task) > 4000:
            raise ValueError("remaining_task must be nonempty and at most 4000 characters")
        packet = self.packet(job_id)
        if state_token != packet["state_token"]:
            raise ValueError("Workspace state changed since inspection; get a fresh resume packet")
        if not packet["retry_available"]:
            raise ValueError("Recovery is blocked: review diagnostic, pending transactions, workspace, or retry depth")
        record, workspace = self._validated(job_id)
        claim = self.checkpoints / f"{job_id}.claimed"
        try:
            fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise ValueError("This job already had its one allowed recovery start") from None
        started = False
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(str(time.time()))
            config = Config.load()
            if config.workspace.resolve() != Path(record["base_workspace"]):
                raise ValueError("DeepSeek provider workspace differs from the checkpoint")
            config = replace(config, workspace=workspace,
                             expected_workspace_identity=record["workspace_identity"],
                             model=record["model"])
            capability = record["capability"]
            if capability not in ("coding", "readonly"):
                raise ValueError("Unknown checkpoint capability")
            config = configure_delegation(config, CODING_PROFILE if capability == "coding" else READONLY_PROFILE)
            context = ("Prior job failed after partial work. Its original goal was: " + record["task"][:1500]
                       + "\nReview current files; preserve completed changes. Use narrow searches and bounded output.")
            manager = DeepSeekJobManager()
            manager._strategy_recovery_parent = job_id
            result = manager.start("Complete only this unfinished work: " + remaining_task, context, config)
            started = True
            child_id = result["job_id"]
            self.managers[child_id] = manager
            return {"parent_job_id": job_id, "workspace": str(workspace), **result}
        except Exception:
            # Preserve the claim if a worker started; never create an invisible second attempt.
            if not started:
                claim.unlink(missing_ok=True)
            raise

    def status(self, job_id: str) -> dict:
        return self.managers[job_id].status(job_id)

    def result(self, job_id: str) -> dict:
        manager = self.managers[job_id]
        payload, usage = manager.result_with_usage_claim(job_id)
        if usage:
            from deepseek_mcp.server import _record_usage
            task_length, outcome = usage
            manager.finish_usage_record(job_id, _record_usage(task_length, outcome))
        return payload

    def message(self, job_id: str, message: str) -> dict:
        return self.managers[job_id].send_message(job_id, message)

    def cancel(self, job_id: str) -> dict:
        return self.managers[job_id].cancel(job_id)


def register_recovery_tools(server, data_dir: Path) -> None:
    from deepseek_mcp.server import (_AGENT_CONTROL, _AGENT_EXECUTION, _LOCAL_CANCELLATION,
                                     _READ_ONLY, _RESULT_WITH_BOOKKEEPING)
    recovery = Recovery(data_dir)

    def reply(fn, *args) -> str:
        try:
            return json.dumps({"ok": True, **fn(*args)}, ensure_ascii=False)
        except (ValueError, KeyError, JobError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)

    @server.tool(annotations=_READ_ONLY)
    def get_deepseek_resume_packet(job_id: str) -> str:
        """Inspect a failed job's bounded checkpoint, changed paths, and recovery blockers."""
        return reply(recovery.packet, job_id)

    @server.tool(annotations=_AGENT_EXECUTION)
    def start_deepseek_resume(job_id: str, remaining_task: str, state_token: str) -> str:
        """Start one focused continuation using a fresh inspected resume packet."""
        return reply(recovery.start, job_id, remaining_task, state_token)

    @server.tool(annotations=_READ_ONLY)
    def get_deepseek_resume_status(job_id: str) -> str:
        """Inspect a resumed DeepSeek job."""
        return reply(recovery.status, job_id)

    @server.tool(annotations=_RESULT_WITH_BOOKKEEPING)
    def get_deepseek_resume_result(job_id: str) -> str:
        """Collect a resumed DeepSeek job result."""
        return reply(recovery.result, job_id)

    @server.tool(annotations=_AGENT_CONTROL)
    def send_deepseek_resume_message(job_id: str, message: str) -> str:
        """Steer a running resumed DeepSeek job."""
        return reply(recovery.message, job_id, message)

    @server.tool(annotations=_LOCAL_CANCELLATION)
    def cancel_deepseek_resume(job_id: str) -> str:
        """Cancel a running resumed DeepSeek job."""
        return reply(recovery.cancel, job_id)
