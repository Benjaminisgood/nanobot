"""CommandOp kernel.

A CLI invocation is a durable operation with authoritative params, a frozen
argv sha, and approval bound to that sha. TemplateRun is the parent progress
bar. TAP registration is a scaffold: red-line ids stay opaque, and live CLI
flags wait on ``taptap --describe-json``.
"""

from nanobot.agent.command_op.events import CommandOpEvent, TemplateRunEvent
from nanobot.agent.command_op.models import (
    Approval,
    ArgvSnapshot,
    Artifact,
    CommandContract,
    CommandExecution,
    CommandOp,
    CommandOpError,
    CommandParam,
    CommandRun,
    OpStatus,
)
from nanobot.agent.command_op.runtime import CommandOpRuntime
from nanobot.agent.command_op.tap_adapter import TapAdapter
from nanobot.agent.command_op.template_run import TemplateRun
from nanobot.agent.command_op.workbench import CommandWorkbench

__all__ = [
    "Approval",
    "ArgvSnapshot",
    "Artifact",
    "CommandContract",
    "CommandExecution",
    "CommandOp",
    "CommandOpError",
    "CommandOpEvent",
    "CommandOpRuntime",
    "CommandParam",
    "CommandRun",
    "CommandWorkbench",
    "OpStatus",
    "TapAdapter",
    "TemplateRun",
    "TemplateRunEvent",
]
