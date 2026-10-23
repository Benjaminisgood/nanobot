"""Kernel events for a CommandOp. These are not chat messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nanobot.events import AgentEvent

CommandOpEventName = Literal[
    "op.created",
    "op.updated",
    "op.need_input",
    "op.need_approval",
    "op.approved",
    "op.started",
    "op.finished",
    "op.cancelled",
]

TemplateRunEventName = Literal["run.progress"]

# Tool-result statuses that mean "this CLI is a CommandOp", not success or failure.
# ``done`` is the terminal tool disposition; the op status stays succeeded/failed/cancelled.
COMMAND_OP_TOOL_STATUSES = frozenset({
    "need_input",
    "need_approval",
    "running",
    "done",
    "approved",
})


@dataclass(frozen=True)
class CommandOpEvent(AgentEvent):
    """A CommandOp lifecycle fact for local subscribers and future workbench UI.

    Delivery stays on ``MessageBus.publish`` (in-process). The event is not a
    channel chat message: ``notification_is_deliverable`` does not admit it,
    and the outbound text fallback is empty.
    """

    name: CommandOpEventName
    op_id: str
    status: str
    blocked_reason: str | None
    contract_ref: str
    argv_sha256: str | None = None
    parent_run_id: str | None = None
    step_id: str | None = None


@dataclass(frozen=True)
class TemplateRunEvent(AgentEvent):
    """Parent-run progress for the boss bar. Same bus as CommandOp, not a chat message.

    ``accepted`` stays false when a step's exit code is 0. Scientific acceptance
    is a separate checkbox and is not implied by ``ready_for_acceptance``.
    """

    name: TemplateRunEventName
    run_id: str
    template: str
    done: int
    total: int
    current_step: str | None
    status: str
    accepted: bool
