"""Built-in contract for the first CommandOp slice.

Unresolved live TAP catalog names from the template map (``averaging_count``,
a blank CLI, and positional indexes pending ``taptap --describe-json``) stay
in the future TAP adapter. This catalog is fully specified and is not a TAP
science rule.
"""

from __future__ import annotations

from nanobot.agent.command_op.models import (
    Artifact,
    CommandContract,
    CommandExecution,
    CommandOp,
    CommandParam,
)

# One writes_new contract so the first path must freeze argv and bind approval.
# ``red_line_ids`` are opaque adapter ids. The kernel does not interpret them.
DEMO_WRITE_NOTE = CommandContract(
    app="command_op_demo",
    command="write_note",
    side_effects="writes_new",
    red_line_ids=("adapter.output_is_new_path",),
    executable=("command-op",),
    params=(
        CommandParam(name="title", type="string", required=True, flag="--title"),
        CommandParam(name="body", type="string", required=True, flag="--body"),
        CommandParam(name="output_dir", type="path", required=True, flag="--output"),
    ),
)


class DemoWriteNoteExecutor:
    """Record a receipt for the demo contract. This does not invoke a CLI."""

    async def execute(self, op: CommandOp, argv: tuple[str, ...]) -> CommandExecution:
        del argv
        sha = "" if op.argv_snapshot is None else op.argv_snapshot.sha256
        return CommandExecution(
            exit_code=0,
            log_uri=f"command-op://{op.op_id}",
            artifacts=(Artifact(path=f"command-op://{op.op_id}/receipt", sha=sha),),
        )
