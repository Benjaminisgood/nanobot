"""TAP registration scaffold. No science checks and no guessed CLI flags.

``register_contract``, ``register_executor``, and ``set_red_line_hook`` forward
to the kernel. Boss-flow commands stay in ``pending`` until
``taptap --describe-json`` supplies flag and positional names. Red-line ids
are opaque strings; this module does not interpret them.
"""

from __future__ import annotations

from dataclasses import dataclass

from nanobot.agent.command_op.models import CommandContract
from nanobot.agent.command_op.runtime import CommandExecutor, CommandOpRuntime, RedLineHook
from nanobot.agent.command_op.template_run import template_specs

# TODO: taptap --describe-json flag and positional names are unknown.
# Pending commands are listed and are not registered.
DESCRIBE_JSON_TODO = (
    "TODO: taptap --describe-json flag and positional names are unknown; "
    "this command is not registered."
)


@dataclass(frozen=True)
class PendingCli:
    """A boss-flow command waiting on a live catalog export."""

    template: str
    step_id: str
    name: str
    summary: str
    app: str
    command: str
    red_line_ids: tuple[str, ...]
    describe_json_todo: str
    also: tuple[tuple[str, str], ...] = ()


class TapAdapter:
    """The three kernel registration hooks, and nothing that runs TAP."""

    def __init__(self, runtime: CommandOpRuntime) -> None:
        self._runtime = runtime
        self._registered: list[str] = []

    def register_contract(
        self,
        contract: CommandContract,
        executor: CommandExecutor | None = None,
    ) -> None:
        """Register a contract the caller fully specified. Flags are not invented here."""
        self._runtime.register_contract(contract, executor)
        self._registered.append(contract.ref)

    def register_executor(self, contract_ref: str, executor: CommandExecutor) -> None:
        self._runtime.register_executor(contract_ref, executor)

    def set_red_line_hook(self, hook: RedLineHook) -> None:
        """Install an adapter hook. Returned strings become ``need_input`` errors.

        The kernel does not read ``red_line_ids``. The hook may echo them; this
        scaffold does not decide what they mean.
        """
        self._runtime.set_red_line_hook(hook)

    def pending(self, template: str) -> tuple[PendingCli, ...]:
        return tuple(
            PendingCli(
                template=template,
                step_id=spec.step_id,
                name=spec.name,
                summary=spec.summary,
                app=spec.app,
                command=spec.command,
                red_line_ids=spec.red_line_ids,
                describe_json_todo=DESCRIBE_JSON_TODO,
                also=spec.also,
            )
            for spec in template_specs(template)
        )

    @property
    def registered_refs(self) -> tuple[str, ...]:
        return tuple(self._registered)
