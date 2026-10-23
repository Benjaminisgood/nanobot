"""CommandOp kernel: state machine, argv freeze, approval binding, one run."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.command_op.catalog import DEMO_WRITE_NOTE
from nanobot.agent.command_op.events import CommandOpEvent
from nanobot.agent.command_op.models import (
    CommandContract,
    CommandExecution,
    CommandOp,
    CommandOpError,
    CommandParam,
    OpStatus,
    apply_approval_policy,
    fill_op,
)
from nanobot.agent.command_op.runtime import CommandOpRuntime
from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.execution import execute_tool_calls
from nanobot.bus.notification_delivery import notification_is_deliverable
from nanobot.bus.outbound_events import outbound_message_for_event
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import ToolCallRequest
from nanobot.utils.progress_events import build_tool_event_finish_payloads

_NOTE = {
    "title": "baseline",
    "body": "peek",
    "output_dir": "notes",
}


def _runtime(tmp_path: Path) -> CommandOpRuntime:
    runtime = CommandOpRuntime(tmp_path)
    runtime.load_builtin_catalog()
    return runtime


def _sha(argv: list[str] | tuple[str, ...]) -> str:
    payload = json.dumps(list(argv), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _RecordingExecutor:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] = ()

    async def execute(self, op: CommandOp, argv: tuple[str, ...]) -> CommandExecution:
        del op
        self.argv = argv
        return CommandExecution(exit_code=0, log_uri="memory://recorded")


@pytest.mark.asyncio
async def test_state_transitions_cover_fill_approval_and_cancel(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    missing, created = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params={"title": "only"},
    )
    assert missing.status == OpStatus.NEED_INPUT
    assert missing.blocked_reason == "need_input"
    assert missing.argv_snapshot is None
    assert missing.approval is None
    assert [event.name for event in created] == ["op.created", "op.need_input"]

    filled, filled_events = await runtime.patch_params(missing.op_id, _NOTE)
    assert filled.status == OpStatus.AWAITING_APPROVAL
    assert filled.blocked_reason == "need_approval"
    assert filled.argv_snapshot is not None
    assert [event.name for event in filled_events] == ["op.need_approval"]
    sha = filled.argv_snapshot.sha256

    with pytest.raises(CommandOpError, match="sha"):
        await runtime.approve(filled.op_id, sha="0" * 64, by="user")
    assert runtime.get(filled.op_id).status == OpStatus.AWAITING_APPROVAL
    assert runtime.get(filled.op_id).approval is None

    approved, approved_events = await runtime.approve(filled.op_id, sha=sha, by="user")
    assert approved.status == OpStatus.APPROVED
    assert approved.blocked_reason is None
    assert approved_events[0].name == "op.approved"
    assert approved.approval is not None
    assert approved.approval.bound_sha == sha

    edited, edited_events = await runtime.patch_params(
        approved.op_id,
        {**_NOTE, "title": "next"},
    )
    assert edited_events[0].name == "op.updated"
    assert edited_events[0].status == OpStatus.APPROVAL_STALE.value
    assert edited_events[0].blocked_reason == "need_input"
    assert edited_events[1].name == "op.need_approval"
    assert edited.approval is None
    assert edited.argv_snapshot is not None
    assert edited.argv_snapshot.sha256 != sha
    with pytest.raises(CommandOpError, match="sha"):
        await runtime.approve(edited.op_id, sha=sha, by="user")

    waiting, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
    )
    cancelled, cancel_events = await runtime.cancel(waiting.op_id)
    assert cancelled.status == OpStatus.CANCELLED
    assert cancel_events[0].name == "op.cancelled"
    with pytest.raises(CommandOpError, match="cancel"):
        await runtime.cancel(waiting.op_id)
    with pytest.raises(CommandOpError, match="edit"):
        await runtime.patch_params(waiting.op_id, _NOTE)

    running = replace(filled, status=OpStatus.RUNNING)
    runtime.store.save(running)
    with pytest.raises(CommandOpError, match="edit"):
        await runtime.patch_params(filled.op_id, _NOTE)

    draft = CommandOp(
        op_id="draft-1",
        contract_ref=DEMO_WRITE_NOTE.ref,
        status=OpStatus.DRAFT,
    )
    ready = fill_op(draft, DEMO_WRITE_NOTE, _NOTE)
    assert ready.status == OpStatus.READY
    assert ready.argv_snapshot is not None
    waiting_policy, policy_event = apply_approval_policy(
        ready,
        DEMO_WRITE_NOTE,
        now="2026-10-23T00:00:00+00:00",
        auto_approve_readonly=True,
    )
    assert waiting_policy.status == OpStatus.AWAITING_APPROVAL
    assert policy_event.name == "op.need_approval"


@pytest.mark.asyncio
async def test_readonly_auto_approve_binds_the_same_sha(tmp_path: Path) -> None:
    contract = CommandContract(
        app="command_op_demo",
        command="peek",
        side_effects="read_only",
        red_line_ids=("adapter.peek",),
        params=(CommandParam(name="path", type="path", required=True, flag="--path"),),
    )
    runtime = CommandOpRuntime(tmp_path, auto_approve_readonly=True)
    runtime.register_contract(contract, _RecordingExecutor())
    op, events = await runtime.propose(
        app="command_op_demo",
        command="peek",
        params={"path": "sample.wyx"},
    )
    assert op.status == OpStatus.APPROVED
    assert op.approval is not None
    assert op.argv_snapshot is not None
    assert op.approval.by == "policy:auto_approve_readonly"
    assert op.approval.bound_sha == op.argv_snapshot.sha256
    assert [event.name for event in events] == ["op.created", "op.approved"]

    manual = CommandOpRuntime(tmp_path / "manual", auto_approve_readonly=False)
    manual.register_contract(contract, _RecordingExecutor())
    waiting, _events = await manual.propose(
        app="command_op_demo",
        command="peek",
        params={"path": "sample.wyx"},
    )
    assert waiting.status == OpStatus.AWAITING_APPROVAL
    assert waiting.approval is None


@pytest.mark.asyncio
async def test_argv_freeze_is_stable_and_sha256_matches_canonical_json(tmp_path: Path) -> None:
    contract = CommandContract(
        app="demo_app",
        command="demo_cmd",
        side_effects="writes_new",
        executable=("runner",),
        params=(
            CommandParam(name="source", type="path", required=True, positional=0),
            CommandParam(name="output_dir", type="path", required=True, flag="--output"),
            CommandParam(name="count", type="integer", required=True, flag="--count"),
            CommandParam(name="json", type="boolean", required=False, flag="--json"),
            CommandParam(
                name="unit",
                type="string",
                required=True,
                flag="--unit",
                enum=("bar", "mbar"),
            ),
        ),
    )
    runtime = CommandOpRuntime(tmp_path)
    runtime.register_contract(contract)
    params = {
        "source": "in.wyx",
        "output_dir": "out",
        "count": 2,
        "json": True,
        "unit": "mbar",
    }
    first, _events = await runtime.propose(app="demo_app", command="demo_cmd", params=params)
    second, _again = await runtime.propose(app="demo_app", command="demo_cmd", params=params)
    assert first.argv_snapshot is not None
    assert second.argv_snapshot is not None
    expected = (
        "runner",
        "demo_app",
        "demo_cmd",
        "in.wyx",
        "--output",
        "out",
        "--count",
        "2",
        "--json",
        "--unit",
        "mbar",
    )
    assert first.argv_snapshot.argv == expected
    assert first.argv_snapshot.sha256 == _sha(expected)
    assert second.argv_snapshot.sha256 == first.argv_snapshot.sha256

    changed, _changed_events = await runtime.propose(
        app="demo_app",
        command="demo_cmd",
        params={**params, "unit": "bar"},
    )
    assert changed.argv_snapshot is not None
    assert changed.argv_snapshot.sha256 != first.argv_snapshot.sha256

    hidden, _hidden_events = await runtime.propose(
        app="demo_app",
        command="demo_cmd",
        params={**params, "json": False},
    )
    assert hidden.argv_snapshot is not None
    assert "--json" not in hidden.argv_snapshot.argv

    invalid, invalid_events = await runtime.propose(
        app="demo_app",
        command="demo_cmd",
        params={**params, "unit": "psi"},
    )
    assert invalid.status == OpStatus.NEED_INPUT
    assert invalid.argv_snapshot is None
    assert any("unit" in error for error in invalid.validation_errors)
    assert invalid_events[-1].name == "op.need_input"


@pytest.mark.asyncio
async def test_approval_binding_rejects_mutated_argv(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    op, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
    )
    assert op.argv_snapshot is not None
    sha = op.argv_snapshot.sha256
    approved, _approved_events = await runtime.approve(op.op_id, sha=sha, by="user")
    assert approved.approval is not None
    snapshot = approved.argv_snapshot
    assert snapshot is not None

    runtime.store.save(replace(
        approved,
        argv_snapshot=replace(snapshot, argv=(*snapshot.argv, "--extra")),
    ))
    with pytest.raises(CommandOpError, match="argv"):
        await runtime.execute(approved.op_id)
    assert runtime.get(approved.op_id).status == OpStatus.APPROVED

    runtime.store.save(replace(
        approved,
        argv_snapshot=replace(snapshot, sha256="ab" * 32),
    ))
    with pytest.raises(CommandOpError, match="bound"):
        await runtime.execute(approved.op_id)
    assert runtime.get(approved.op_id).status == OpStatus.APPROVED

    runtime.store.save(replace(approved, params={**approved.params, "title": "mutated"}))
    with pytest.raises(CommandOpError, match="argv"):
        await runtime.execute(approved.op_id)
    persisted = runtime.get(approved.op_id)
    assert persisted.status == OpStatus.APPROVED
    assert persisted.approval is not None
    assert persisted.approval.bound_sha == sha


@pytest.mark.asyncio
async def test_happy_path_run_persists_receipt(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    op, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
        parent_run_id="template-1",
    )
    assert op.argv_snapshot is not None
    assert op.parent_run_id == "template-1"
    approved, _approved = await runtime.approve(
        op.op_id,
        sha=op.argv_snapshot.sha256,
        by="user",
    )
    finished = await runtime.execute(approved.op_id)
    assert finished.status == OpStatus.SUCCEEDED
    assert finished.blocked_reason is None
    assert finished.run is not None
    assert finished.run.exit_code == 0
    assert finished.run.receipt_id == finished.op_id
    assert finished.run.started_at is not None
    assert finished.run.ended_at is not None
    assert finished.run.log_uri == f"command-op://{finished.op_id}"
    assert finished.argv_snapshot is not None
    assert finished.artifacts[0].sha == finished.argv_snapshot.sha256
    names = [event.name for event in runtime.events if event.op_id == finished.op_id]
    assert names == ["op.created", "op.need_approval", "op.approved", "op.started", "op.finished"]
    assert await runtime.execute(finished.op_id) == finished

    reloaded = CommandOpRuntime(tmp_path).get(finished.op_id)
    assert reloaded.status == OpStatus.SUCCEEDED
    assert reloaded.parent_run_id == "template-1"
    assert reloaded.run is not None
    assert reloaded.run.receipt_id == finished.op_id
    with pytest.raises(CommandOpError, match="cancel"):
        await runtime.cancel(finished.op_id)


@pytest.mark.asyncio
async def test_red_line_ids_stay_adapter_hooks(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    contract = runtime.contract("command_op_demo", "write_note")
    assert contract.red_line_ids == ("adapter.output_is_new_path",)
    op, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
    )
    assert op.status == OpStatus.AWAITING_APPROVAL
    assert op.validation_errors == ()

    def hook(seen: CommandContract, params: dict[str, object]) -> list[str]:
        del params
        return [f"adapter:{seen.red_line_ids[0]}"]

    runtime.set_red_line_hook(hook)
    blocked, events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
    )
    assert blocked.status == OpStatus.NEED_INPUT
    assert blocked.validation_errors == ("adapter:adapter.output_is_new_path",)
    assert events[-1].name == "op.need_input"
    assert blocked.argv_snapshot is None


@pytest.mark.asyncio
async def test_agent_loop_suspends_propose_and_runs_after_approval(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.aclose = AsyncMock()
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    seen: list[CommandOpEvent] = []

    async def record(event: CommandOpEvent) -> None:
        seen.append(event)

    bus.subscribe(record, CommandOpEvent)
    try:
        assert {"propose_op", "get_op", "patch_op_params"} <= set(loop.tools.tool_names)
        results, events = await execute_tool_calls(
            loop.tools,
            [ToolCallRequest(
                id="call-1",
                name="propose_op",
                arguments={
                    "app": "command_op_demo",
                    "command": "write_note",
                    "params": _NOTE,
                    "parent_run_id": "template-1",
                },
            )],
            concurrent=False,
            external_lookup_counts={},
            workspace_violation_counts={},
            hook=AgentHook(),
            context=AgentHookContext(iteration=0, messages=[]),
        )
        assert events[0]["status"] == "need_approval"
        assert events[0]["status"] not in {"ok", "error"}
        observation = json.loads(results[0])
        assert observation["kind"] == "command_op"
        assert observation["status"] == "awaiting_approval"
        assert observation["blocked_reason"] == "need_approval"
        assert observation["cli_dispatched"] is False
        assert observation["parent_run_id"] == "template-1"
        assert "op.need_approval" in observation["events"]
        assert bus.outbound_size == 0
        assert any(event.name == "op.need_approval" for event in seen)
        assert notification_is_deliverable(
            CommandOpEvent,
            channel="websocket",
            publish_lifecycle=True,
        ) is False
        chat = outbound_message_for_event(
            channel="websocket",
            chat_id="c",
            event=seen[0],
        )
        assert chat.content == ""
        assert isinstance(chat.event, CommandOpEvent)

        progress = build_tool_event_finish_payloads(AgentHookContext(
            iteration=0,
            messages=[],
            tool_calls=[ToolCallRequest(id="call-1", name="propose_op", arguments={})],
            tool_results=list(results),
            tool_events=list(events),
        ))[0]
        assert progress["phase"] == "need_approval"
        assert progress["error"] is None
        assert progress["disposition"] == "need_approval"
        assert progress["result"] == results[0]

        sha = observation["argv_sha256"]
        assert isinstance(sha, str)
        approved = await loop.approve_command_op(observation["op_id"], sha=sha, by="user")
        assert approved.status == OpStatus.APPROVED
        background = list(loop._background_tasks)
        assert background
        await asyncio.gather(*background)
        finished = loop.command_ops.get(observation["op_id"])
        assert finished.status == OpStatus.SUCCEEDED
        assert finished.run is not None
        assert finished.run.exit_code == 0
        assert any(event.name == "op.finished" for event in seen)
        assert bus.outbound_size == 0
    finally:
        await loop.aclose()


def test_plain_tool_progress_is_unchanged() -> None:
    payload = build_tool_event_finish_payloads(AgentHookContext(
        iteration=0,
        messages=[],
        tool_calls=[ToolCallRequest(id="ok", name="read_file", arguments={})],
        tool_results=["contents"],
        tool_events=[{"name": "read_file", "status": "ok", "detail": "contents"}],
    ))[0]
    assert payload["phase"] == "end"
    assert payload["result"] == "contents"
    assert payload["error"] is None
    assert "disposition" not in payload

    failed = build_tool_event_finish_payloads(AgentHookContext(
        iteration=0,
        messages=[],
        tool_calls=[ToolCallRequest(id="bad", name="read_file", arguments={})],
        tool_results=["Error: missing"],
        tool_events=[{"name": "read_file", "status": "error", "detail": "missing"}],
    ))[0]
    assert failed["phase"] == "error"
    assert failed["result"] is None
    assert failed["error"] == "Error: missing"
    assert "disposition" not in failed
