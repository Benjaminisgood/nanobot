"""Model tools for CommandOp. They read and write the op; they do not run the CLI."""

# pyright: reportIncompatibleMethodOverride=false, reportAbstractUsage=false

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.command_op.events import CommandOpEvent
from nanobot.agent.command_op.models import CommandOp, CommandOpError, tool_disposition
from nanobot.agent.command_op.runtime import CommandOpRuntime
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.schema import ObjectSchema, StringSchema, tool_parameters_schema


class CommandOpSuspension(ToolResult):
    """Tool output whose status is a CommandOp disposition, not CLI success or failure."""

    command_op_disposition: str

    def __new__(cls, content: str, *, disposition: str) -> CommandOpSuspension:
        created = str.__new__(cls, content)
        if not isinstance(created, cls):
            raise TypeError("CommandOpSuspension was not constructed")
        created.is_error = False
        created.command_op_disposition = disposition
        return created


def _suspension(
    runtime: CommandOpRuntime,
    op: CommandOp,
    events: tuple[CommandOpEvent, ...],
) -> CommandOpSuspension:
    payload = runtime.observation(op, events)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return CommandOpSuspension(content, disposition=tool_disposition(op.status))


def _failure(exc: CommandOpError) -> ToolResult:
    return ToolResult.error(f"Error: {exc.message}")


@tool_parameters(
    tool_parameters_schema(
        required=["app", "command", "params"],
        app=StringSchema("Contract app id, for example command_op_demo."),
        command=StringSchema("Contract command id, for example write_note."),
        params=ObjectSchema(
            description=(
                "Authoritative form values. This object replaces the op params; "
                "it does not run the command."
            ),
            additional_properties=True,
        ),
        parent_run_id=StringSchema(
            "Optional parent TemplateRun id. When that run exists, step_id selects its step.",
            nullable=True,
        ),
        step_id=StringSchema(
            "Boss-flow step id for parent_run_id, such as pulse_size or y_procedure.",
            nullable=True,
        ),
    )
)
class ProposeOpTool(Tool):
    """Create a CommandOp. Pending fill or approval is a suspension, not a finished CLI call."""

    _plugin_discoverable = False

    def __init__(self, runtime: CommandOpRuntime) -> None:
        self._runtime = runtime

    @property
    def name(self) -> str:
        return "propose_op"

    @property
    def description(self) -> str:
        return (
            "Create a CommandOp for a registered contract. Params written here are "
            "authoritative. This does not run the command. need_input and need_approval "
            "mean the op is suspended, not that a CLI succeeded or failed."
        )

    async def execute(
        self,
        app: str,
        command: str,
        params: dict[str, Any],
        parent_run_id: str | None = None,
        step_id: str | None = None,
    ) -> ToolResult:
        try:
            op, events = await self._runtime.propose(
                app=app,
                command=command,
                params=params,
                parent_run_id=parent_run_id,
                step_id=step_id,
            )
        except CommandOpError as exc:
            return _failure(exc)
        return _suspension(self._runtime, op, events)


@tool_parameters(
    tool_parameters_schema(
        required=["op_id"],
        op_id=StringSchema("CommandOp id returned by propose_op."),
    )
)
class GetOpTool(Tool):
    """Read the authoritative CommandOp. Do not invent params the op does not store."""

    _plugin_discoverable = False

    def __init__(self, runtime: CommandOpRuntime) -> None:
        self._runtime = runtime

    @property
    def name(self) -> str:
        return "get_op"

    @property
    def description(self) -> str:
        return (
            "Read one CommandOp. The returned status, params, and argv sha are "
            "authoritative. A suspended op has not run its command."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, op_id: str) -> ToolResult:
        try:
            op = self._runtime.get(op_id)
        except CommandOpError as exc:
            return _failure(exc)
        return _suspension(self._runtime, op, ())


@tool_parameters(
    tool_parameters_schema(
        required=["op_id", "params"],
        op_id=StringSchema("CommandOp id to update."),
        params=ObjectSchema(
            description=(
                "Full replacement for the authoritative params. Any previous argv "
                "freeze and approval are invalidated."
            ),
            additional_properties=True,
        ),
    )
)
class PatchOpParamsTool(Tool):
    """Replace params. Clears the frozen argv and any approval bound to the old sha."""

    _plugin_discoverable = False

    def __init__(self, runtime: CommandOpRuntime) -> None:
        self._runtime = runtime

    @property
    def name(self) -> str:
        return "patch_op_params"

    @property
    def description(self) -> str:
        return (
            "Replace a CommandOp's authoritative params. This drops the frozen argv "
            "and any approval bound to the previous sha. It does not run the command."
        )

    async def execute(self, op_id: str, params: dict[str, Any]) -> ToolResult:
        try:
            op, events = await self._runtime.patch_params(op_id, params)
        except CommandOpError as exc:
            return _failure(exc)
        return _suspension(self._runtime, op, events)


def register_command_op_tools(
    registry: ToolRegistry,
    runtime: CommandOpRuntime,
) -> list[str]:
    """Attach the model interface from draft section 5. Approval is not a model tool."""
    tools: tuple[Tool, ...] = (
        ProposeOpTool(runtime),
        GetOpTool(runtime),
        PatchOpParamsTool(runtime),
    )
    names: list[str] = []
    for tool in tools:
        registry.register(tool)
        names.append(tool.name)
    return names
