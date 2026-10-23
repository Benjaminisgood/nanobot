"""CommandContract, CommandOp, and the single-op state machine.

Statuses follow draft v0 section 3. ``blocked_reason`` is the coarser wait
signal from section 2.2: ``need_input``, ``need_approval``, ``running``, or
none. Terminal success is ``succeeded`` (tool disposition ``done``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Literal, cast

from nanobot.agent.command_op.events import CommandOpEvent, CommandOpEventName

SideEffect = Literal["read_only", "writes_new", "mutates", "instrument"]
ParamType = Literal["string", "integer", "number", "boolean", "path"]
ParamValue = str | int | float | bool

_SIDE_EFFECTS = frozenset({"read_only", "writes_new", "mutates", "instrument"})
_PARAM_TYPES = frozenset({"string", "integer", "number", "boolean", "path"})


class CommandOpError(Exception):
    """A CommandOp transition was rejected. The persisted op is unchanged."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OpStatus(str, Enum):
    """Draft section 3 status. Stored as its value string."""

    DRAFT = "draft"
    NEED_INPUT = "need_input"
    READY = "ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVAL_STALE = "approval_stale"
    APPROVED = "approved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_LOCKED_STATUSES = frozenset({
    OpStatus.RUNNING,
    OpStatus.SUCCEEDED,
    OpStatus.FAILED,
    OpStatus.CANCELLED,
})


def blocked_reason_for(status: OpStatus) -> str | None:
    """Section 2.2 wait signal. Terminal and approved ops are not blocked."""
    if status in {OpStatus.DRAFT, OpStatus.NEED_INPUT, OpStatus.APPROVAL_STALE}:
        return "need_input"
    if status == OpStatus.AWAITING_APPROVAL:
        return "need_approval"
    if status == OpStatus.RUNNING:
        return "running"
    return None


def can_fill(status: OpStatus) -> bool:
    """Params stay editable until an executor has started."""
    return status not in _LOCKED_STATUSES


def can_cancel(status: OpStatus) -> bool:
    """A finished op stays finished. A running op can still be cancelled."""
    return status not in {OpStatus.SUCCEEDED, OpStatus.FAILED, OpStatus.CANCELLED}


def tool_disposition(status: OpStatus) -> str:
    """Map an op status onto a tool event that is neither CLI success nor failure."""
    blocked = blocked_reason_for(status)
    if blocked is not None:
        return blocked
    if status in {OpStatus.SUCCEEDED, OpStatus.FAILED, OpStatus.CANCELLED}:
        return "done"
    if status == OpStatus.READY:
        return "need_approval"
    return "approved"


@dataclass(frozen=True)
class CommandParam:
    """One contract field. Exactly one of ``flag`` or ``positional`` is set."""

    name: str
    type: ParamType
    required: bool = False
    enum: tuple[str, ...] | None = None
    flag: str | None = None
    positional: int | None = None


@dataclass(frozen=True)
class CommandContract:
    """Static capability. TAP adapters register these; the kernel does not infer science."""

    app: str
    command: str
    side_effects: SideEffect
    params: tuple[CommandParam, ...] = ()
    red_line_ids: tuple[str, ...] = ()
    executable: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.app}/{self.command}"


@dataclass(frozen=True)
class ArgvSnapshot:
    """Argv frozen after validation. ``sha256`` binds later approval."""

    argv: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class Approval:
    """User or policy approval. ``bound_sha`` must equal the frozen argv sha."""

    bound_sha: str
    by: str
    at: str


@dataclass(frozen=True)
class Artifact:
    """Registered output. ``sha`` reuses the FormalRun artifact field name."""

    path: str
    sha: str


@dataclass(frozen=True)
class CommandRun:
    """Executor receipt. ``receipt_id`` is this kernel's ``op_id`` wrapper."""

    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    log_uri: str | None = None
    receipt_id: str | None = None


@dataclass(frozen=True)
class CommandExecution:
    """What an adapter executor returns. The kernel writes this onto the op."""

    exit_code: int
    log_uri: str | None = None
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class CommandOp:
    """One concrete invocation. ``params`` are authoritative; argv is a freeze of them."""

    op_id: str
    contract_ref: str
    status: OpStatus
    params: dict[str, ParamValue] = field(default_factory=dict)
    argv_snapshot: ArgvSnapshot | None = None
    approval: Approval | None = None
    run: CommandRun | None = None
    artifacts: tuple[Artifact, ...] = ()
    parent_run_id: str | None = None
    step_id: str | None = None
    validation_errors: tuple[str, ...] = ()

    @property
    def blocked_reason(self) -> str | None:
        return blocked_reason_for(self.status)


def argv_sha256(argv: Sequence[str]) -> str:
    """SHA-256 of the canonical JSON array of argv strings (UTF-8, no spaces)."""
    payload = json.dumps(list(argv), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_contract(contract: CommandContract) -> None:
    """Reject a contract the kernel cannot schedule. Does not read red-line ids."""
    if not contract.app or not contract.command:
        raise CommandOpError("app and command are required")
    if "/" in contract.app or "/" in contract.command:
        raise CommandOpError("app and command cannot contain '/'")
    if contract.side_effects not in _SIDE_EFFECTS:
        raise CommandOpError(f"invalid side_effects {contract.side_effects!r}")
    for token in contract.executable:
        if not token:
            raise CommandOpError("executable tokens must be non-empty strings")
    names: set[str] = set()
    positions: set[int] = set()
    for param in contract.params:
        if not param.name or param.name in names:
            raise CommandOpError(f"duplicate or empty param {param.name!r}")
        names.add(param.name)
        if param.type not in _PARAM_TYPES:
            raise CommandOpError(f"{param.name} has unsupported type {param.type!r}")
        has_flag = param.flag is not None
        has_positional = param.positional is not None
        if has_flag == has_positional:
            raise CommandOpError(f"{param.name} needs exactly one of flag or positional")
        if param.flag is not None and not param.flag.startswith("-"):
            raise CommandOpError(f"{param.name} flag must start with '-'")
        if param.positional is not None:
            if param.positional < 0 or param.positional in positions:
                raise CommandOpError(f"{param.name} has an invalid positional index")
            positions.add(param.positional)


def parse_params(raw: object) -> dict[str, ParamValue]:
    """Normalize an untrusted params object. Nested values are rejected."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CommandOpError("params must be an object")
    parsed: dict[str, ParamValue] = {}
    for key, value in cast(dict[object, object], raw).items():
        if not isinstance(key, str):
            raise CommandOpError("param names must be strings")
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, str):
            parsed[key] = value
            continue
        if isinstance(value, int):
            parsed[key] = value
            continue
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise CommandOpError(f"{key} must be finite")
            parsed[key] = value
            continue
        raise CommandOpError(f"{key} must be a string, number, or boolean")
    return parsed


def validate_params(
    contract: CommandContract,
    params: Mapping[str, ParamValue],
) -> list[str]:
    """Schema checks only. Red-line ids are not interpreted here."""
    known = {param.name: param for param in contract.params}
    errors: list[str] = []
    for name in params:
        if name not in known:
            errors.append(f"unknown param {name}")
    for param in contract.params:
        if param.name not in params:
            if param.required:
                errors.append(f"missing required {param.name}")
            continue
        errors.extend(_value_errors(param, params[param.name]))
    return errors


def freeze_argv(
    contract: CommandContract,
    params: Mapping[str, ParamValue],
) -> ArgvSnapshot:
    """Build argv from the contract mapping. Params stay the source of truth."""
    argv: list[str] = list(contract.executable)
    argv.append(contract.app)
    argv.append(contract.command)
    positionals = sorted(
        (param for param in contract.params if param.positional is not None),
        key=lambda param: param.positional or 0,
    )
    for param in positionals:
        value = params.get(param.name)
        if value is None:
            continue
        argv.append(_argv_token(param, value))
    for param in contract.params:
        if param.flag is None or param.name not in params:
            continue
        value = params[param.name]
        if param.type == "boolean":
            if value:
                argv.append(param.flag)
            continue
        argv.append(param.flag)
        argv.append(_argv_token(param, value))
    frozen = tuple(argv)
    return ArgvSnapshot(argv=frozen, sha256=argv_sha256(frozen))


def fill_op(
    op: CommandOp,
    contract: CommandContract,
    params: Mapping[str, ParamValue],
    *,
    red_line_errors: Sequence[str] = (),
) -> CommandOp:
    """Validate and freeze. Invalid input lands on ``need_input`` and drops the snapshot."""
    _ensure_editable(op)
    if contract.ref != op.contract_ref:
        raise CommandOpError("contract does not match the op")
    errors = validate_params(contract, params)
    errors.extend(red_line_errors)
    if errors:
        return replace(
            op,
            status=OpStatus.NEED_INPUT,
            params=dict(params),
            argv_snapshot=None,
            approval=None,
            validation_errors=tuple(errors),
        )
    return replace(
        op,
        status=OpStatus.READY,
        params=dict(params),
        argv_snapshot=freeze_argv(contract, params),
        approval=None,
        validation_errors=(),
    )


def apply_approval_policy(
    op: CommandOp,
    contract: CommandContract,
    *,
    now: str,
    auto_approve_readonly: bool,
) -> tuple[CommandOp, CommandOpEvent]:
    """Move a ready op to awaiting approval, or bind a read-only auto approval."""
    if op.status != OpStatus.READY or op.argv_snapshot is None:
        raise CommandOpError("approval policy applies to a ready op with frozen argv")
    if contract.side_effects == "read_only" and auto_approve_readonly:
        approved = replace(
            op,
            status=OpStatus.APPROVED,
            approval=Approval(
                bound_sha=op.argv_snapshot.sha256,
                by="policy:auto_approve_readonly",
                at=now,
            ),
        )
        return approved, _event(approved, "op.approved")
    waiting = replace(op, status=OpStatus.AWAITING_APPROVAL)
    return waiting, _event(waiting, "op.need_approval")


def propose_op(
    contract: CommandContract,
    params: Mapping[str, ParamValue],
    *,
    op_id: str,
    now: str,
    parent_run_id: str | None,
    auto_approve_readonly: bool,
    red_line_errors: Sequence[str] = (),
    step_id: str | None = None,
) -> tuple[CommandOp, tuple[CommandOpEvent, ...]]:
    draft = CommandOp(
        op_id=op_id,
        contract_ref=contract.ref,
        status=OpStatus.DRAFT,
        parent_run_id=parent_run_id,
        step_id=step_id,
    )
    filled = fill_op(draft, contract, params, red_line_errors=red_line_errors)
    if filled.status == OpStatus.NEED_INPUT:
        return filled, (_event(filled, "op.created"), _event(filled, "op.need_input"))
    final, policy_event = apply_approval_policy(
        filled,
        contract,
        now=now,
        auto_approve_readonly=auto_approve_readonly,
    )
    return final, (_event(final, "op.created"), policy_event)


def patch_op(
    op: CommandOp,
    contract: CommandContract,
    params: Mapping[str, ParamValue],
    *,
    now: str,
    auto_approve_readonly: bool,
    red_line_errors: Sequence[str] = (),
) -> tuple[CommandOp, tuple[CommandOpEvent, ...]]:
    """Replace authoritative params. Any prior argv freeze and approval die first."""
    _ensure_editable(op)
    events: list[CommandOpEvent] = []
    current = op
    if op.argv_snapshot is not None or op.approval is not None:
        current = replace(
            op,
            status=OpStatus.APPROVAL_STALE,
            argv_snapshot=None,
            approval=None,
            validation_errors=(),
        )
        events.append(_event(current, "op.updated"))
    filled = fill_op(current, contract, params, red_line_errors=red_line_errors)
    if filled.status == OpStatus.NEED_INPUT:
        events.append(_event(filled, "op.need_input"))
        return filled, tuple(events)
    final, policy_event = apply_approval_policy(
        filled,
        contract,
        now=now,
        auto_approve_readonly=auto_approve_readonly,
    )
    events.append(policy_event)
    return final, tuple(events)


def approve_op(
    op: CommandOp,
    *,
    sha: str,
    by: str,
    now: str,
) -> tuple[CommandOp, CommandOpEvent]:
    """Bind approval to the frozen argv sha. A mismatch leaves the op untouched."""
    if op.status != OpStatus.AWAITING_APPROVAL:
        raise CommandOpError(f"cannot approve a {op.status} op")
    if op.argv_snapshot is None:
        raise CommandOpError("cannot approve an op without a frozen argv")
    if sha != op.argv_snapshot.sha256:
        raise CommandOpError("approval sha does not match the frozen argv")
    if not by.strip():
        raise CommandOpError("approval actor is required")
    approved = replace(
        op,
        status=OpStatus.APPROVED,
        approval=Approval(bound_sha=sha, by=by, at=now),
    )
    return approved, _event(approved, "op.approved")


def begin_run(
    op: CommandOp,
    contract: CommandContract,
    *,
    now: str,
) -> tuple[CommandOp, CommandOpEvent]:
    """Start an executor only when approval is still bound to the rebuilt argv."""
    if op.status != OpStatus.APPROVED:
        raise CommandOpError(f"cannot run a {op.status} op")
    if op.approval is None or op.argv_snapshot is None:
        raise CommandOpError("approved op is missing its argv approval binding")
    if contract.ref != op.contract_ref:
        raise CommandOpError("contract does not match the op")
    if op.approval.bound_sha != op.argv_snapshot.sha256:
        raise CommandOpError("approval is not bound to the frozen argv")
    rebuilt = freeze_argv(contract, op.params)
    if rebuilt != op.argv_snapshot:
        raise CommandOpError("argv snapshot does not match authoritative params")
    running = replace(
        op,
        status=OpStatus.RUNNING,
        run=CommandRun(started_at=now, receipt_id=op.op_id),
    )
    return running, _event(running, "op.started")


def finish_run(
    op: CommandOp,
    result: CommandExecution,
    *,
    now: str,
) -> tuple[CommandOp, CommandOpEvent]:
    if op.status != OpStatus.RUNNING or op.run is None:
        raise CommandOpError(f"cannot finish a {op.status} op")
    exit_code = result.exit_code
    if isinstance(exit_code, bool):
        raise CommandOpError("exit_code must be an integer")
    for artifact in result.artifacts:
        if not artifact.path or not artifact.sha:
            raise CommandOpError("artifact path and sha are required")
    status = OpStatus.SUCCEEDED if exit_code == 0 else OpStatus.FAILED
    finished = replace(
        op,
        status=status,
        run=replace(
            op.run,
            ended_at=now,
            exit_code=exit_code,
            log_uri=result.log_uri,
        ),
        artifacts=result.artifacts,
    )
    return finished, _event(finished, "op.finished")


def cancel_op(op: CommandOp, *, now: str) -> tuple[CommandOp, CommandOpEvent]:
    if op.status in {OpStatus.SUCCEEDED, OpStatus.FAILED, OpStatus.CANCELLED}:
        raise CommandOpError(f"cannot cancel a {op.status} op")
    run = op.run
    if run is not None and run.ended_at is None:
        run = replace(run, ended_at=now)
    cancelled = replace(op, status=OpStatus.CANCELLED, run=run)
    return cancelled, _event(cancelled, "op.cancelled")


def command_op_to_dict(op: CommandOp) -> dict[str, Any]:
    snapshot = op.argv_snapshot
    approval = op.approval
    run = op.run
    return {
        "op_id": op.op_id,
        "contract_ref": op.contract_ref,
        "status": op.status.value,
        "params": dict(op.params),
        "argv_snapshot": None if snapshot is None else {
            "argv": list(snapshot.argv),
            "sha256": snapshot.sha256,
        },
        "approval": None if approval is None else {
            "bound_sha": approval.bound_sha,
            "by": approval.by,
            "at": approval.at,
        },
        "run": None if run is None else {
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "exit_code": run.exit_code,
            "log_uri": run.log_uri,
            "receipt_id": run.receipt_id,
        },
        "artifacts": [{"path": item.path, "sha": item.sha} for item in op.artifacts],
        "parent_run_id": op.parent_run_id,
        "step_id": op.step_id,
        "validation_errors": list(op.validation_errors),
    }


def command_op_from_dict(data: object) -> CommandOp:
    """Parse a persisted record. The file is an untrusted boundary."""
    record = _object(data, "command op")
    try:
        status = OpStatus(_require_str(record, "status"))
    except ValueError as exc:
        raise CommandOpError(f"unknown command op status {record.get('status')!r}") from exc
    return CommandOp(
        op_id=_require_str(record, "op_id"),
        contract_ref=_require_str(record, "contract_ref"),
        status=status,
        params=_parse_stored_params(record.get("params")),
        argv_snapshot=_parse_snapshot(record.get("argv_snapshot")),
        approval=_parse_approval(record.get("approval")),
        run=_parse_run(record.get("run")),
        artifacts=_parse_artifacts(record.get("artifacts")),
        parent_run_id=_optional_str(record.get("parent_run_id"), "parent_run_id"),
        step_id=_optional_str(record.get("step_id"), "step_id"),
        validation_errors=_string_tuple(record.get("validation_errors"), "validation_errors"),
    )


def _ensure_editable(op: CommandOp) -> None:
    if op.status in _LOCKED_STATUSES:
        raise CommandOpError(f"cannot edit a {op.status} op")


def _value_errors(param: CommandParam, value: ParamValue) -> list[str]:
    if param.type in {"string", "path"}:
        if not isinstance(value, str) or not value.strip():
            return [f"{param.name} must be a non-empty string"]
    elif param.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{param.name} must be an integer"]
    elif param.type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return [f"{param.name} must be a number"]
    elif param.type == "boolean":
        if not isinstance(value, bool):
            return [f"{param.name} must be a boolean"]
    else:
        return [f"{param.name} has unsupported type {param.type}"]
    if param.enum is not None and str(value) not in param.enum:
        return [f"{param.name} must be one of {list(param.enum)}"]
    return []


def _argv_token(param: CommandParam, value: ParamValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _event(op: CommandOp, name: CommandOpEventName) -> CommandOpEvent:
    snapshot = op.argv_snapshot
    return CommandOpEvent(
        name=name,
        op_id=op.op_id,
        status=op.status.value,
        blocked_reason=op.blocked_reason,
        contract_ref=op.contract_ref,
        argv_sha256=None if snapshot is None else snapshot.sha256,
        parent_run_id=op.parent_run_id,
        step_id=op.step_id,
    )


def _object(data: object, label: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CommandOpError(f"{label} must be an object")
    parsed: dict[str, Any] = {}
    for key, value in cast(dict[object, object], data).items():
        if not isinstance(key, str):
            raise CommandOpError(f"{label} keys must be strings")
        parsed[key] = value
    return parsed


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CommandOpError(f"{label} must be a list of strings")
    items: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str):
            raise CommandOpError(f"{label} must be a list of strings")
        items.append(item)
    return tuple(items)


def _require_str(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise CommandOpError(f"command op field {key} must be a string")
    return value


def _optional_str(value: object, key: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    raise CommandOpError(f"command op field {key} must be a string or null")


def _parse_stored_params(value: object) -> dict[str, ParamValue]:
    return parse_params(value if value is not None else {})


def _parse_snapshot(value: object) -> ArgvSnapshot | None:
    if value is None:
        return None
    record = _object(value, "argv_snapshot")
    sha = record.get("sha256")
    if not isinstance(sha, str) or not sha:
        raise CommandOpError("argv_snapshot.sha256 must be a string")
    return ArgvSnapshot(argv=_string_list(record.get("argv"), "argv_snapshot.argv"), sha256=sha)


def _parse_approval(value: object) -> Approval | None:
    if value is None:
        return None
    record = _object(value, "approval")
    return Approval(
        bound_sha=_require_str(record, "bound_sha"),
        by=_require_str(record, "by"),
        at=_require_str(record, "at"),
    )


def _parse_run(value: object) -> CommandRun | None:
    if value is None:
        return None
    record = _object(value, "run")
    exit_code = record.get("exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise CommandOpError("run.exit_code must be an integer or null")
    return CommandRun(
        started_at=_optional_str(record.get("started_at"), "started_at"),
        ended_at=_optional_str(record.get("ended_at"), "ended_at"),
        exit_code=exit_code,
        log_uri=_optional_str(record.get("log_uri"), "log_uri"),
        receipt_id=_optional_str(record.get("receipt_id"), "receipt_id"),
    )


def _parse_artifacts(value: object) -> tuple[Artifact, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CommandOpError("artifacts must be a list")
    items: list[Artifact] = []
    for entry in cast(list[object], value):
        record = _object(entry, "artifact")
        items.append(Artifact(path=_require_str(record, "path"), sha=_require_str(record, "sha")))
    return tuple(items)


def _string_tuple(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    return _string_list(value, key)
