"""CommandOp kernel (draft v0, sections 2–5).

A CLI invocation is a durable operation with authoritative params, a frozen
argv sha, and approval bound to that sha. TemplateRun progress and TAP
science rules are adapter follow-ups, not part of this package.
"""

from nanobot.agent.command_op.events import CommandOpEvent
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
    "OpStatus",
]
