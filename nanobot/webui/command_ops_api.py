"""WebUI snapshot and mutations for CommandOp fill, approve, and cancel.

Mutations call ``AgentLoop.fill_command_op``, ``approve_command_op``, and
``cancel_command_op``. Those methods publish the existing CommandOp and
TemplateRun events. Approval requires the sha from ``op.need_approval``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from nanobot.agent.command_op.events import CommandOpEvent, TemplateRunEvent
from nanobot.agent.command_op.models import CommandOpError
from nanobot.agent.command_op.runtime import CommandOpRuntime
from nanobot.agent.command_op.tap_adapter import TapAdapter
from nanobot.agent.command_op.template_run import TemplateRun
from nanobot.agent.command_op.workbench import CommandWorkbench

_OP_ORDER = {
    "awaiting_approval": 0,
    "need_input": 1,
    "approval_stale": 2,
    "draft": 3,
    "ready": 4,
    "approved": 5,
    "running": 6,
}


class CommandOpsHost(Protocol):
    """The AgentLoop surface the WebUI is allowed to call."""

    command_ops: CommandOpRuntime
    tap_adapter: TapAdapter

    def command_workbench(self) -> CommandWorkbench: ...

    async def fill_command_op(self, op_id: str, params: object) -> object: ...

    async def approve_command_op(self, op_id: str, *, sha: str, by: str) -> object: ...

    async def cancel_command_op(self, op_id: str) -> object: ...

    async def start_template_run(self, template: str, *, run_id: str | None = None) -> object: ...


def command_op_wire(event: CommandOpEvent) -> dict[str, object]:
    """Websocket body for one CommandOp event. ``argv_sha256`` is the approval binding."""
    return {
        "event": "command_op",
        "name": event.name,
        "op_id": event.op_id,
        "status": event.status,
        "blocked_reason": event.blocked_reason,
        "contract_ref": event.contract_ref,
        "argv_sha256": event.argv_sha256,
        "parent_run_id": event.parent_run_id,
        "step_id": event.step_id,
    }


def template_run_wire(event: TemplateRunEvent) -> dict[str, object]:
    return {
        "event": "template_run",
        "name": event.name,
        "run_id": event.run_id,
        "template": event.template,
        "done": event.done,
        "total": event.total,
        "current_step": event.current_step,
        "status": event.status,
        "accepted": event.accepted,
    }


def command_ops_snapshot(host: CommandOpsHost) -> dict[str, object]:
    workbench = host.command_workbench()
    runtime = host.command_ops
    ops = [workbench.op_view(op.op_id) for op in runtime.list_ops()]
    ops.sort(key=lambda item: (_OP_ORDER.get(str(item["status"]), 9), str(item["op_id"])))
    runs = [workbench.run_view(run.run_id) for run in runtime.list_template_runs()]
    pending: list[dict[str, object]] = []
    for template in ("quantitative", "kinetics"):
        for item in host.tap_adapter.pending(template):
            pending.append({
                "template": item.template,
                "step_id": item.step_id,
                "name": item.name,
                "summary": item.summary,
                "app": item.app,
                "command": item.command,
                "describe_json_todo": item.describe_json_todo,
                "catalog_source": item.catalog_source,
            })
    return {
        "catalog": {
            "status": runtime.catalog_status,
            "detail": runtime.catalog_detail,
        },
        "ops": ops,
        "runs": runs,
        "pending": pending,
    }


async def dispatch_command_ops(
    host: CommandOpsHost,
    action: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Apply one workbench action and return the refreshed snapshot."""
    if action == "snapshot":
        return command_ops_snapshot(host)
    if action == "fill":
        op_id = _op_id(payload)
        await host.fill_command_op(op_id, payload.get("params"))
        return _with_op(host, op_id)
    if action == "approve":
        op_id = _op_id(payload)
        sha = payload.get("sha")
        if not isinstance(sha, str) or not sha.strip():
            raise CommandOpError("approval sha is required")
        await host.approve_command_op(op_id, sha=sha, by="user")
        return _with_op(host, op_id)
    if action == "cancel":
        op_id = _op_id(payload)
        await host.cancel_command_op(op_id)
        return _with_op(host, op_id)
    if action == "start_run":
        template = payload.get("template")
        if not isinstance(template, str) or not template.strip():
            raise CommandOpError("template is required")
        run = await host.start_template_run(template)
        if not isinstance(run, TemplateRun):
            raise CommandOpError("template run was not created")
        snapshot = command_ops_snapshot(host)
        snapshot["run"] = host.command_workbench().run_view(run.run_id)
        return snapshot
    raise CommandOpError(f"unknown command op action {action}")


def _with_op(host: CommandOpsHost, op_id: str) -> dict[str, object]:
    snapshot = command_ops_snapshot(host)
    snapshot["op"] = host.command_workbench().op_view(op_id)
    return snapshot


def _op_id(payload: Mapping[str, object]) -> str:
    op_id = payload.get("op_id")
    if not isinstance(op_id, str) or not op_id.strip():
        raise CommandOpError("op_id is required")
    return op_id
