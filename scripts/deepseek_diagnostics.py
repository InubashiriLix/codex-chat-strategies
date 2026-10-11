"""Persist safe DeepSeek failure categories across Codex sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from deepseek_mcp.config import Config
from deepseek_mcp.job_manager import DeepSeekJobManager

JOB_ID = re.compile(r"[0-9a-f]{12}\Z")
MAX_RECORD_BYTES = 4096


def classify_error(error: str | None, status: str = "failed") -> dict[str, str]:
    value = (error or "").lower()
    if "run token budget" in value:
        return {
            "kind": "local_token_budget",
            "detail": "Single-job cumulative token guard reached (1,000,000 tokens); this is not evidence of insufficient API balance.",
            "next_step": "Inspect retained changes, then delegate smaller remaining units and bound tool output.",
        }
    if "conversation history budget" in value:
        return {"kind": "local_history_budget", "detail": "Provider conversation history grew beyond the local guard.", "next_step": "Start a focused new job with a short summary of completed work."}
    if "maximum turns" in value or "max_turns" in value:
        return {"kind": "local_turn_limit", "detail": "The configured per-job turn limit was reached.", "next_step": "Inspect completed changes and split the remaining work."}
    if "status=402" in value:
        return {"kind": "provider_balance", "detail": "DeepSeek returned HTTP 402 (insufficient balance).", "next_step": "Check the account balance before retrying."}
    if "status=401" in value:
        return {"kind": "provider_auth", "detail": "DeepSeek returned HTTP 401 (authentication failed).", "next_step": "Check the locally stored API key without sending it to chat."}
    if "status=429" in value or "category=rate_limit" in value:
        return {"kind": "provider_rate_limit", "detail": "DeepSeek returned a rate or concurrency limit.", "next_step": "Pause or reduce concurrent requests, then retry only the unfinished unit."}
    if "category=connection" in value or "unreachable" in value:
        return {"kind": "network", "detail": "The provider connection failed.", "next_step": "Check connectivity and provider status; do not repeat paid work blindly."}
    if "status=500" in value or "status=503" in value:
        return {"kind": "provider_server", "detail": "DeepSeek reported a server-side failure.", "next_step": "Check whether files changed, then retry the unfinished unit later."}
    if "workspace" in value and ("busy" in value or "owned" in value):
        return {"kind": "workspace_busy", "detail": "The workspace execution lease was already held.", "next_step": "Inspect the active job or use a separate prepared worktree."}
    if "recovery" in value or "transaction" in value:
        return {"kind": "mutation_recovery", "detail": "A workspace mutation needs review or acknowledgement.", "next_step": "Inspect actual files and recovery records before retrying."}
    if status == "cancelled":
        return {"kind": "cancelled", "detail": "The job was cancelled.", "next_step": "Inspect files before starting a replacement job."}
    return {"kind": "other", "detail": "The worker failed without a recognized safe category.", "next_step": "Inspect the job result and local worker log; preserve changed files."}


def write_diagnostic(
    directory: Path, job_id: str, status: str, error: str | None,
    *, source: str = "automatic", occurred_at: str | None = None,
) -> dict:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid DeepSeek job ID")
    if source not in {"automatic", "historical_user_report"}:
        raise ValueError("Invalid diagnostic source")
    record = {
        "job_id": job_id,
        "status": status,
        "source": source,
        "timestamp": occurred_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **classify_error(error, status),
    }
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Diagnostic record is too large")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".diagnostic-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, directory / f"{job_id}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return record


def read_diagnostic(directory: Path, job_id: str) -> dict:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid DeepSeek job ID")
    path = directory / f"{job_id}.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {"found": False, "job_id": job_id}
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("Diagnostic record is not a private regular file")
        payload = os.read(descriptor, MAX_RECORD_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Diagnostic record is too large")
    value = json.loads(payload)
    if value.get("job_id") != job_id or value.get("source") not in {"automatic", "historical_user_report"}:
        raise ValueError("Diagnostic record is invalid")
    # Return only the known fields; never surface arbitrary data from a file.
    return {"found": True, **{key: value.get(key) for key in
            ("job_id", "status", "source", "timestamp", "kind", "detail", "next_step")}}


def install_job_diagnostics(directory: Path) -> None:
    original = DeepSeekJobManager._finish_job
    if getattr(original, "_strategy_diagnostics", False):
        return

    def finish(self, job_id, *, desired_status, result, error, lease, preserve_mutation_error=False):
        original(
            self, job_id, desired_status=desired_status, result=result,
            error=error, lease=lease, preserve_mutation_error=preserve_mutation_error,
        )
        try:
            snapshot = self.status(job_id)
            if snapshot["status"] != "completed":
                write_diagnostic(directory, job_id, snapshot["status"], snapshot.get("error"))
        except Exception:
            # Diagnostics must never affect the job's committed outcome.
            pass

    finish._strategy_diagnostics = True
    DeepSeekJobManager._finish_job = finish


def check_balance() -> dict:
    import httpx

    config = Config.load()
    if config.base_url.rstrip("/") != "https://api.deepseek.com":
        return {"checked": False, "reason": "custom provider endpoint"}
    try:
        response = httpx.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": "Bearer " + config.api_key}, timeout=15,
        )
    except httpx.HTTPError:
        return {"checked": False, "reason": "network error"}
    if response.status_code == 200:
        try:
            available = response.json().get("is_available")
        except ValueError:
            return {"checked": False, "reason": "invalid balance response"}
        return {"checked": True, "http_status": 200, "is_available": available is True}
    return {"checked": True, "http_status": response.status_code, **classify_error(f"status={response.status_code}")}


def register_diagnostic_tools(server, data_dir: Path) -> None:
    from deepseek_mcp.server import _READ_ONLY

    directory = data_dir / "diagnostics"
    install_job_diagnostics(directory)

    @server.tool(annotations=_READ_ONLY)
    def get_deepseek_diagnostic(job_id: str) -> str:
        """Retrieve a safe failure category saved across Codex sessions."""
        try:
            for _ in range(5):
                record = read_diagnostic(directory, job_id)
                if record["found"]:
                    break
                time.sleep(0.05)
            return json.dumps({"ok": True, **record}, ensure_ascii=False)
        except (ValueError, OSError, json.JSONDecodeError) as error:
            return json.dumps({"ok": False, "error": type(error).__name__})

    @server.tool(annotations=_READ_ONLY)
    def check_deepseek_balance() -> str:
        """Check official DeepSeek balance availability without a model call or key disclosure."""
        try:
            return json.dumps({"ok": True, **check_balance()}, ensure_ascii=False)
        except (RuntimeError, ValueError):
            return json.dumps({"ok": False, "error": "local configuration unavailable"})
