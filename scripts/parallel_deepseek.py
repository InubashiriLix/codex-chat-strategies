"""Five isolated DeepSeek job slots backed by Git worktrees.

Loaded by chat-mcp.py inside the pinned upstream worker runtime. No provider
request is made while preparing or inspecting slots.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import subprocess
from threading import RLock
import uuid

from deepseek_mcp.config import Config
from deepseek_mcp.execution_profile import CODING_PROFILE, READONLY_PROFILE, configure_delegation
from deepseek_mcp.job_manager import DeepSeekJobManager, JobError
from deepseek_mcp.model_selection import resolve_profile
from deepseek_mcp.transaction_recovery import acknowledge_with_lease, load_recovery_config, query_with_lease

SLOT_COUNT = 5


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@dataclass
class Slot:
    path: Path
    manager: DeepSeekJobManager


class ParallelDeepSeek:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._lock = RLock()
        self.batch_id: str | None = None
        self.root: Path | None = None
        self.base_head: str | None = None
        self.slots: dict[int, Slot] = {}
        self.jobs: dict[str, int] = {}
        self.collected: set[str] = set()

    def _base(self) -> Config:
        config = load_recovery_config()
        if self.root is not None and config.workspace.resolve() != self.root:
            raise ValueError("Codex workspace changed; restart the MCP server before using these slots")
        return config

    def prepare(self) -> dict:
        """Create five independent worktrees from a clean committed baseline."""
        with self._lock:
            if self.batch_id is not None:
                return self._describe()
            root = self._base().workspace.resolve()
            if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
                raise ValueError("Run Codex from the Git worktree root for parallel DeepSeek")
            if _git(root, "status", "--porcelain", "--untracked-files=all"):
                raise ValueError("Commit or otherwise resolve working-tree changes before preparing parallel slots")
            head = _git(root, "rev-parse", "HEAD")
            identity = hashlib.sha256(str(root).encode()).hexdigest()[:16]
            batch_id = uuid.uuid4().hex[:12]
            parent = self.data_dir / "parallel-worktrees" / identity / batch_id
            parent.mkdir(parents=True, exist_ok=False, mode=0o700)
            created: dict[int, Slot] = {}
            try:
                for number in range(1, SLOT_COUNT + 1):
                    path = parent / f"lane-{number}"
                    _git(root, "worktree", "add", "--detach", str(path), head)
                    created[number] = Slot(path, DeepSeekJobManager())
            except Exception:
                # No jobs have started yet. Remove only the fresh, clean lanes.
                for slot in created.values():
                    subprocess.run(
                        ["git", "-C", str(root), "worktree", "remove", str(slot.path)],
                        capture_output=True, check=False,
                    )
                raise
            self.root = root
            self.base_head = head
            self.batch_id = batch_id
            self.slots = created
            return self._describe()

    def _describe(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "base_workspace": str(self.root),
            "base_head": self.base_head,
            "lanes": {str(n): str(slot.path) for n, slot in self.slots.items()},
            "capacity": SLOT_COUNT,
        }

    def _slot(self, batch_id: str, lane: int) -> Slot:
        self._base()
        if batch_id != self.batch_id or lane not in self.slots:
            raise ValueError("Unknown parallel batch or lane; prepare slots first")
        slot = self.slots[lane]
        if not slot.path.is_dir():
            raise ValueError(f"Lane {lane} worktree is missing: {slot.path}")
        return slot

    def _config(self, slot: Slot, readonly: bool, model: str = "flash") -> Config:
        self._base()
        config = Config.load()
        if config.workspace.resolve() != self.root:
            raise ValueError("DeepSeek provider workspace does not match the prepared batch")
        config = replace(config, workspace=slot.path, expected_workspace_identity=None)
        config = configure_delegation(config, READONLY_PROFILE if readonly else CODING_PROFILE)
        config.model, config.reasoning_effort = resolve_profile(
            model,
            flash_model=config.flash_model, pro_model=config.pro_model,
            flash_effort=config.flash_reasoning_effort, pro_effort=config.pro_reasoning_effort,
        )
        return config

    def _recovery_config(self, slot: Slot) -> Config:
        return replace(self._base(), workspace=slot.path, expected_workspace_identity=None)

    def start(self, batch_id: str, lane: int, task: str, context: str = "", model: str = "flash", readonly: bool = False) -> dict:
        with self._lock:
            slot = self._slot(batch_id, lane)
            config = self._config(slot, readonly, model)
            result = slot.manager.start(task, context, config)
            self.jobs[result["job_id"]] = lane
            return {"batch_id": batch_id, "lane": lane, "workspace": str(slot.path), **result}

    def _job(self, job_id: str) -> tuple[int, Slot]:
        with self._lock:
            self._base()
            lane = self.jobs.get(job_id)
            if lane is None or lane not in self.slots:
                raise ValueError("Unknown parallel DeepSeek job ID")
            return lane, self.slots[lane]

    def status(self, job_id: str) -> dict:
        lane, slot = self._job(job_id)
        return {"lane": lane, "workspace": str(slot.path), **slot.manager.status(job_id)}

    def message(self, job_id: str, message: str) -> dict:
        lane, slot = self._job(job_id)
        return {"lane": lane, **slot.manager.send_message(job_id, message)}

    def cancel(self, job_id: str) -> dict:
        lane, slot = self._job(job_id)
        return {"lane": lane, **slot.manager.cancel(job_id)}

    def result(self, job_id: str) -> dict:
        lane, slot = self._job(job_id)
        payload, usage = slot.manager.result_with_usage_claim(job_id)
        if usage:
            from deepseek_mcp.server import _record_usage
            task_length, outcome = usage
            slot.manager.finish_usage_record(job_id, _record_usage(task_length, outcome))
        if payload.get("ready"):
            self.collected.add(job_id)
        return {"lane": lane, "workspace": str(slot.path), **payload}

    def recovery(self, batch_id: str, lane: int) -> dict:
        slot = self._slot(batch_id, lane)
        pending = query_with_lease(self._recovery_config(slot))
        return {"lane": lane, "workspace": str(slot.path), "pending": pending, "count": len(pending)}

    def acknowledge(self, batch_id: str, lane: int, transaction_ids: list[str]) -> dict:
        slot = self._slot(batch_id, lane)
        removed, pending = acknowledge_with_lease(self._recovery_config(slot), transaction_ids)
        return {"lane": lane, "acknowledged": removed, "pending": pending}

    def inspect(self, batch_id: str) -> dict:
        with self._lock:
            if batch_id != self.batch_id or self.root is None:
                raise ValueError("Unknown parallel batch")
            lanes = {}
            for number, slot in self.slots.items():
                lanes[str(number)] = {
                    "workspace": str(slot.path),
                    "status": _git(slot.path, "status", "--porcelain", "--untracked-files=all"),
                }
            return {"batch_id": batch_id, "lanes": lanes}

    def close(self, batch_id: str) -> dict:
        """Remove only clean, reviewed worktrees after all jobs finish."""
        with self._lock:
            self._base()
            if batch_id != self.batch_id or self.root is None:
                raise ValueError("Unknown parallel batch")
            for job_id in self.jobs:
                if job_id not in self.collected:
                    raise ValueError("Collect every finished parallel job result before closing")
            for number, slot in self.slots.items():
                if _git(slot.path, "status", "--porcelain", "--untracked-files=all"):
                    raise ValueError(f"Lane {number} has unintegrated changes: {slot.path}")
                if _git(slot.path, "rev-parse", "HEAD") != self.base_head:
                    raise ValueError(f"Lane {number} has commits; integrate and inspect them before manual cleanup")
                if query_with_lease(self._recovery_config(slot)):
                    raise ValueError(f"Lane {number} has unacknowledged mutations")
            for number, slot in list(self.slots.items()):
                _git(self.root, "worktree", "remove", str(slot.path))
                del self.slots[number]
            self.batch_id = None
            self.root = None
            self.base_head = None
            self.jobs.clear()
            self.collected.clear()
            return {"closed": True}


def register_parallel_tools(server, data_dir: Path) -> None:
    from deepseek_mcp.server import (
        _AGENT_CONTROL, _AGENT_EXECUTION, _LOCAL_CANCELLATION,
        _READ_ONLY, _READONLY_AGENT_EXECUTION, _RECOVERY_ACK,
        _RESULT_WITH_BOOKKEEPING,
    )

    coordinator = ParallelDeepSeek(data_dir)

    def reply(fn, *args, **kwargs) -> str:
        try:
            return json.dumps({"ok": True, **fn(*args, **kwargs)}, ensure_ascii=False)
        except (ValueError, JobError, OSError, subprocess.CalledProcessError, RuntimeError) as error:
            return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)

    @server.tool(annotations=_AGENT_EXECUTION)
    def prepare_deepseek_parallel() -> str:
        """Create five separate Git worktrees for concurrent DeepSeek jobs."""
        return reply(coordinator.prepare)

    @server.tool(annotations=_AGENT_EXECUTION)
    def start_deepseek_parallel(batch_id: str, lane: int, task: str, context: str = "", model: str = "flash") -> str:
        """Start a coding job in one prepared lane (1 through 5)."""
        return reply(coordinator.start, batch_id, lane, task, context, model)

    @server.tool(annotations=_READONLY_AGENT_EXECUTION)
    def start_deepseek_parallel_readonly(batch_id: str, lane: int, task: str, context: str = "", model: str = "flash") -> str:
        """Start a read-only job in one prepared lane (1 through 5)."""
        return reply(coordinator.start, batch_id, lane, task, context, model, True)

    @server.tool(annotations=_READ_ONLY)
    def get_deepseek_parallel_status(job_id: str) -> str:
        """Inspect one parallel job without waiting."""
        return reply(coordinator.status, job_id)

    @server.tool(annotations=_AGENT_CONTROL)
    def send_deepseek_parallel_message(job_id: str, message: str) -> str:
        """Send a steering instruction to a running parallel job."""
        return reply(coordinator.message, job_id, message)

    @server.tool(annotations=_LOCAL_CANCELLATION)
    def cancel_deepseek_parallel(job_id: str) -> str:
        """Cancel a running parallel job."""
        return reply(coordinator.cancel, job_id)

    @server.tool(annotations=_RESULT_WITH_BOOKKEEPING)
    def get_deepseek_parallel_result(job_id: str) -> str:
        """Get one parallel job result when ready."""
        return reply(coordinator.result, job_id)

    @server.tool(annotations=_RESULT_WITH_BOOKKEEPING)
    def get_deepseek_parallel_recovery(batch_id: str, lane: int) -> str:
        """List unacknowledged mutations for one lane."""
        return reply(coordinator.recovery, batch_id, lane)

    @server.tool(annotations=_RECOVERY_ACK)
    def acknowledge_deepseek_parallel_mutations(batch_id: str, lane: int, transaction_ids: list[str]) -> str:
        """Acknowledge reviewed transaction IDs for one lane."""
        return reply(coordinator.acknowledge, batch_id, lane, transaction_ids)

    @server.tool(annotations=_READ_ONLY)
    def inspect_deepseek_parallel(batch_id: str) -> str:
        """List worktree paths and Git status for all five lanes."""
        return reply(coordinator.inspect, batch_id)

    @server.tool(annotations=_AGENT_EXECUTION)
    def close_deepseek_parallel(batch_id: str) -> str:
        """Remove lanes only after jobs finish and all worktrees are clean and reviewed."""
        return reply(coordinator.close, batch_id)
