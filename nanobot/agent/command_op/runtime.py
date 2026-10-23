"""Schedule one CommandOp without blocking the agent turn.

Integration points (draft section 5). TapTool and TapRunManager are not in
this tree; a future TAP adapter hangs off the three methods below instead of
growing a second scheduler:

* ``register_contract`` — static catalog entry (app, command, params, red-line ids)
* ``register_executor`` — runs the frozen argv after approval
* ``set_red_line_hook`` — adapter checks. The kernel stores ids and does not interpret them.

``AgentLoop`` owns one runtime, registers ``propose_op`` / ``get_op`` /
``patch_op_params``, and schedules ``execute`` when it sees ``op.approved``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from loguru import logger

from nanobot.agent.command_op.catalog import DEMO_WRITE_NOTE, DemoWriteNoteExecutor
from nanobot.agent.command_op.events import CommandOpEvent
from nanobot.agent.command_op.models import (
    CommandContract,
    CommandExecution,
    CommandOp,
    CommandOpError,
    OpStatus,
    ParamValue,
    approve_op,
    begin_run,
    cancel_op,
    command_op_from_dict,
    command_op_to_dict,
    finish_run,
    parse_params,
    patch_op,
    propose_op,
    validate_contract,
)
from nanobot.bus.queue import MessageBus
from nanobot.utils.helpers import atomic_write_text

RedLineHook = Callable[[CommandContract, Mapping[str, ParamValue]], list[str]]


class CommandExecutor(Protocol):
    """Adapter-owned runner. Receives the frozen argv, not a fresh model translation."""

    async def execute(
        self,
        op: CommandOp,
        argv: tuple[str, ...],
    ) -> CommandExecution: ...


def _no_red_lines(
    contract: CommandContract,
    params: Mapping[str, ParamValue],
) -> list[str]:
    del contract, params
    return []


class CommandOpStore:
    """Durable op records. One JSON file per op, replaced atomically."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, op_id: str) -> Path:
        if not op_id or op_id in {".", ".."} or "/" in op_id or "\\" in op_id:
            raise CommandOpError("invalid op id")
        return self.root / f"{op_id}.json"

    def save(self, op: CommandOp) -> None:
        payload = command_op_to_dict(op)
        atomic_write_text(
            self.path_for(op.op_id),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def get(self, op_id: str) -> CommandOp:
        path = self.path_for(op_id)
        if not path.is_file():
            raise CommandOpError(f"unknown command op {op_id}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandOpError(f"corrupt command op {op_id}") from exc
        return command_op_from_dict(data)


class CommandOpRuntime:
    """Authoritative CommandOp state for one workspace."""

    def __init__(
        self,
        workspace: Path,
        bus: MessageBus | None = None,
        *,
        auto_approve_readonly: bool = True,
    ) -> None:
        self.workspace = workspace
        self.store = CommandOpStore(workspace / "command_ops")
        self.events: list[CommandOpEvent] = []
        self._bus = bus
        self.auto_approve_readonly = auto_approve_readonly
        self._contracts: dict[str, CommandContract] = {}
        self._executors: dict[str, CommandExecutor] = {}
        self._red_line_hook: RedLineHook = _no_red_lines
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[CommandOp]] = {}

    def register_contract(
        self,
        contract: CommandContract,
        executor: CommandExecutor | None = None,
    ) -> None:
        validate_contract(contract)
        self._contracts[contract.ref] = contract
        if executor is not None:
            self._executors[contract.ref] = executor

    def register_executor(self, contract_ref: str, executor: CommandExecutor) -> None:
        if contract_ref not in self._contracts:
            raise CommandOpError(f"unknown contract {contract_ref}")
        self._executors[contract_ref] = executor

    def set_red_line_hook(self, hook: RedLineHook) -> None:
        """Adapter validation. Returned strings become ``need_input`` errors.

        The kernel does not inspect ``contract.red_line_ids``.
        """
        self._red_line_hook = hook

    def load_builtin_catalog(self) -> None:
        """Register the single demo contract. TAP catalogs are not loaded here."""
        self.register_contract(DEMO_WRITE_NOTE, DemoWriteNoteExecutor())

    def contract(self, app: str, command: str) -> CommandContract:
        ref = f"{app}/{command}"
        found = self._contracts.get(ref)
        if found is None:
            raise CommandOpError(f"unknown contract {ref}")
        return found

    async def propose(
        self,
        *,
        app: str,
        command: str,
        params: object = None,
        parent_run_id: str | None = None,
        op_id: str | None = None,
    ) -> tuple[CommandOp, tuple[CommandOpEvent, ...]]:
        parsed = parse_params(params)
        async with self._lock:
            contract = self.contract(app, command)
            op, events = propose_op(
                contract,
                parsed,
                op_id=op_id or str(uuid4()),
                now=self._now(),
                parent_run_id=parent_run_id,
                auto_approve_readonly=self.auto_approve_readonly,
                red_line_errors=self._red_lines(contract, parsed),
            )
            self.store.save(op)
        await self._emit_all(events)
        return op, events

    def get(self, op_id: str) -> CommandOp:
        return self.store.get(op_id)

    async def patch_params(
        self,
        op_id: str,
        params: object,
    ) -> tuple[CommandOp, tuple[CommandOpEvent, ...]]:
        parsed = parse_params(params)
        async with self._lock:
            current = self.store.get(op_id)
            contract = self._require(current.contract_ref)
            op, events = patch_op(
                current,
                contract,
                parsed,
                now=self._now(),
                auto_approve_readonly=self.auto_approve_readonly,
                red_line_errors=self._red_lines(contract, parsed),
            )
            self.store.save(op)
        await self._emit_all(events)
        return op, events

    async def approve(
        self,
        op_id: str,
        *,
        sha: str,
        by: str,
    ) -> tuple[CommandOp, tuple[CommandOpEvent, ...]]:
        async with self._lock:
            current = self.store.get(op_id)
            op, event = approve_op(current, sha=sha, by=by, now=self._now())
            self.store.save(op)
        events = (event,)
        await self._emit_all(events)
        return op, events

    async def cancel(self, op_id: str) -> tuple[CommandOp, tuple[CommandOpEvent, ...]]:
        async with self._lock:
            current = self.store.get(op_id)
            op, event = cancel_op(current, now=self._now())
            self.store.save(op)
        events = (event,)
        await self._emit_all(events)
        return op, events

    async def execute(self, op_id: str) -> CommandOp:
        """Run a previously approved op. Safe to call twice; the second call waits."""
        async with self._lock:
            existing = self._inflight.get(op_id)
            if existing is not None:
                task = existing
            else:
                op = self.store.get(op_id)
                if op.status in {OpStatus.SUCCEEDED, OpStatus.FAILED, OpStatus.CANCELLED}:
                    return op
                if op.status == OpStatus.RUNNING:
                    raise CommandOpError("op is already running")
                contract = self._require(op.contract_ref)
                if contract.ref not in self._executors:
                    raise CommandOpError(f"no executor for {contract.ref}")
                begin_run(op, contract, now=self._now())
                task = asyncio.create_task(self._run(op_id), name=f"command-op-{op_id}")
                self._inflight[op_id] = task
                task.add_done_callback(lambda _done, key=op_id: self._inflight.pop(key, None))
        return await task

    def observation(
        self,
        op: CommandOp,
        events: Sequence[CommandOpEvent] = (),
    ) -> dict[str, object]:
        """Structured tool observation. This is not a user-facing chat message."""
        contract = self._contracts.get(op.contract_ref)
        snapshot = op.argv_snapshot
        approval = op.approval
        return {
            "kind": "command_op",
            "op_id": op.op_id,
            "contract_ref": op.contract_ref,
            "status": op.status.value,
            "blocked_reason": op.blocked_reason,
            "params": dict(op.params),
            "argv": None if snapshot is None else list(snapshot.argv),
            "argv_sha256": None if snapshot is None else snapshot.sha256,
            "approval": None if approval is None else {
                "bound_sha": approval.bound_sha,
                "by": approval.by,
                "at": approval.at,
            },
            "cli_dispatched": op.status in {
                OpStatus.RUNNING,
                OpStatus.SUCCEEDED,
                OpStatus.FAILED,
            },
            "validation_errors": list(op.validation_errors),
            "parent_run_id": op.parent_run_id,
            "side_effects": None if contract is None else contract.side_effects,
            "red_line_ids": [] if contract is None else list(contract.red_line_ids),
            "events": [event.name for event in events],
        }

    async def _run(self, op_id: str) -> CommandOp:
        async with self._lock:
            op = self.store.get(op_id)
            contract = self._require(op.contract_ref)
            executor = self._executors[contract.ref]
            running, started = begin_run(op, contract, now=self._now())
            self.store.save(running)
            snapshot = running.argv_snapshot
        await self._emit_all((started,))
        if snapshot is None:
            raise CommandOpError("running op lost its argv snapshot")
        try:
            result = await executor.execute(running, snapshot.argv)
        except Exception:
            logger.exception("CommandOp {} executor failed", op_id)
            result = CommandExecution(exit_code=1, log_uri=None)
        async with self._lock:
            current = self.store.get(op_id)
            if current.status != OpStatus.RUNNING:
                return current
            finished, event = finish_run(current, result, now=self._now())
            self.store.save(finished)
        await self._emit_all((event,))
        return finished

    def _require(self, contract_ref: str) -> CommandContract:
        found = self._contracts.get(contract_ref)
        if found is None:
            raise CommandOpError(f"unknown contract {contract_ref}")
        return found

    def _red_lines(
        self,
        contract: CommandContract,
        params: Mapping[str, ParamValue],
    ) -> list[str]:
        return list(self._red_line_hook(contract, params))

    async def _emit_all(self, events: Sequence[CommandOpEvent]) -> None:
        for event in events:
            self.events.append(event)
            if self._bus is not None:
                await self._bus.publish(event)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
