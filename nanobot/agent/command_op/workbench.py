"""Workbench hooks for fill, approve, and cancel.

A UI drives an op through these methods. Results are the existing CommandOp
events on ``MessageBus`` (``op.need_input``, ``op.need_approval``,
``op.approved``, ``op.cancelled``), not chat text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nanobot.agent.command_op.events import CommandOpEvent, TemplateRunEvent
from nanobot.agent.command_op.models import (
    CommandOp,
    CommandOpError,
    OpStatus,
    can_cancel,
    can_fill,
)
from nanobot.agent.command_op.runtime import CommandOpRuntime
from nanobot.agent.command_op.template_run import run_phase, template_run_to_dict
from nanobot.bus.queue import MessageBus


class CommandWorkbench:
    """Fill, approve, and cancel against one CommandOp runtime."""

    def __init__(self, runtime: CommandOpRuntime, bus: MessageBus | None = None) -> None:
        self._runtime = runtime
        self._bus = bus

    async def fill(self, op_id: str, params: object) -> CommandOp:
        """Replace authoritative params. A changed form drops the old argv approval."""
        op, _events = await self._runtime.patch_params(op_id, params)
        return op

    async def approve(self, op_id: str, *, sha: str, by: str) -> CommandOp:
        """Bind approval to the argv sha carried on ``op.need_approval``."""
        op, _events = await self._runtime.approve(op_id, sha=sha, by=by)
        return op

    async def cancel(self, op_id: str) -> CommandOp:
        op, _events = await self._runtime.cancel(op_id)
        return op

    def actions(self, op: CommandOp) -> dict[str, bool]:
        return {
            "fill": can_fill(op.status),
            "approve": (
                op.status == OpStatus.AWAITING_APPROVAL and op.argv_snapshot is not None
            ),
            "cancel": can_cancel(op.status),
        }

    def op_view(self, op_id: str) -> dict[str, Any]:
        """Form state a UI needs beside the CommandOp event stream."""
        op = self._runtime.get(op_id)
        snapshot = op.argv_snapshot
        run = op.run
        contract = self._runtime.contract_for(op.contract_ref)
        return {
            "op_id": op.op_id,
            "contract_ref": op.contract_ref,
            "status": op.status.value,
            "blocked_reason": op.blocked_reason,
            "params": dict(op.params),
            "argv_sha256": None if snapshot is None else snapshot.sha256,
            "validation_errors": list(op.validation_errors),
            "parent_run_id": op.parent_run_id,
            "step_id": op.step_id,
            "actions": self.actions(op),
            "fields": [] if contract is None else [
                {
                    "name": param.name,
                    "type": param.type,
                    "required": param.required,
                    "enum": None if param.enum is None else list(param.enum),
                    "flag": param.flag,
                    "positional": param.positional,
                }
                for param in contract.params
            ],
            "run": None if run is None else {
                "exit_code": run.exit_code,
                "log_uri": run.log_uri,
                "started_at": run.started_at,
                "ended_at": run.ended_at,
            },
            "artifacts": [{"path": item.path, "sha": item.sha} for item in op.artifacts],
        }

    def run_view(self, run_id: str) -> dict[str, Any]:
        run = self._runtime.get_template_run(run_id)
        view = template_run_to_dict(run)
        view["status"] = run_phase(run)
        return view

    def suggested_params(self, run_id: str, step_id: str) -> dict[str, str]:
        """Binding results for a later step. Fill still has to submit them."""
        run = self._runtime.get_template_run(run_id)
        suggested: dict[str, str] = {}
        for binding in run.bindings:
            if binding.target_step == step_id:
                suggested[binding.target_field] = binding.artifact_path
        return suggested

    def subscribe(
        self,
        handler: Callable[[CommandOpEvent], Awaitable[None] | None],
    ) -> Callable[[], None]:
        """Listen for fill, approve, and cancel on the existing CommandOp events."""
        if self._bus is None:
            raise CommandOpError("workbench events need a message bus")
        return self._bus.subscribe(handler, CommandOpEvent)

    def subscribe_progress(
        self,
        handler: Callable[[TemplateRunEvent], Awaitable[None] | None],
    ) -> Callable[[], None]:
        """Listen for parent-run progress on the same bus."""
        if self._bus is None:
            raise CommandOpError("workbench events need a message bus")
        return self._bus.subscribe(handler, TemplateRunEvent)
