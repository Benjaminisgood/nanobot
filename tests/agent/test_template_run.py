"""TemplateRun progress, workbench fill/approve/cancel, and TAP registration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.command_op import CommandWorkbench, TapAdapter
from nanobot.agent.command_op.events import CommandOpEvent, TemplateRunEvent
from nanobot.agent.command_op.models import (
    Artifact,
    CommandContract,
    CommandExecution,
    CommandOp,
    CommandOpError,
    CommandParam,
    OpStatus,
)
from nanobot.agent.command_op.runtime import CommandOpRuntime
from nanobot.agent.command_op.template_run import (
    run_phase,
    select_artifact,
    template_specs,
)
from nanobot.agent.command_op.workbench import CommandWorkbench as Workbench
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.command_op import ProposeOpTool
from nanobot.bus.notification_delivery import notification_is_deliverable
from nanobot.bus.queue import MessageBus

_NOTE = {
    "title": "baseline",
    "body": "peek",
    "output_dir": "notes",
}

_QUANT_FLOW = (
    ("init_tap", "项目就绪", "有没有配置", "project_config", "init_tap"),
    ("wyx_summary", "抽查 WYX", "数据能不能用", "wyx_inspect", "wyx_summary"),
    ("fit_baseline", "基线拟合", "基线漂多少", "fit_baseline_levels", "fit_baseline"),
    ("pulse_size", "脉冲量", "每脉冲多少 mol", "process_pulse_sizes", "pulse_size"),
    ("process_quantitative", "定量面积", "峰面积", "process_quantitative_areas", "process_quantitative"),
    ("analyze_reaction", "反应核算", "转化 / 选择 / 元素收支", "process_reaction_analysis", "analyze_reaction"),
    ("product_distributions", "出图", "标准图", "plot_reaction_analysis", "product_distributions"),
)

_KINETICS_FLOW = (
    ("init_tap", "项目就绪", "有没有配置"),
    ("wyx_summary", "抽查 WYX", "数据能不能用"),
    ("pulse_size", "脉冲量", "脉冲量（表或固定值，二选一）"),
    ("y_procedure", "Y-procedure", "出口流 → 浓度/速率"),
    ("concentrations", "浓度图", "出图包（可多选）"),
    ("rates", "速率图", "出图包（可多选）"),
    ("rate_concentration_correlations", "rate–c 相关", "出图包（可多选）"),
    ("accumulation_balances", "累积收支", "出图包（可多选）"),
    ("filter_review", "滤波复核", "出图包（可多选）"),
)

_COMMAND_OP_EVENTS = {
    "op.created",
    "op.updated",
    "op.need_input",
    "op.need_approval",
    "op.approved",
    "op.started",
    "op.finished",
    "op.cancelled",
}


def _runtime(tmp_path: Path, bus: MessageBus | None = None) -> CommandOpRuntime:
    runtime = CommandOpRuntime(tmp_path, bus=bus)
    runtime.load_builtin_catalog()
    runtime.register_executor("command_op_demo/write_note", _StepArtifacts())
    return runtime


class _StepArtifacts:
    async def execute(self, op: CommandOp, argv: tuple[str, ...]) -> CommandExecution:
        del argv
        step = op.step_id or "note"
        paths = {
            "fit_baseline": "out/baseline",
            "pulse_size": "out/pulse_size.csv",
            "process_quantitative": "out/response_areas.csv",
        }
        return CommandExecution(
            exit_code=0,
            log_uri=f"memory://{step}",
            artifacts=(Artifact(path=paths.get(step, f"out/{step}"), sha=f"sha-{step}"),),
        )


class _ExitCode:
    def __init__(self, code: int) -> None:
        self.code = code

    async def execute(self, op: CommandOp, argv: tuple[str, ...]) -> CommandExecution:
        del op, argv
        return CommandExecution(exit_code=self.code)


async def _finish_step(runtime: CommandOpRuntime, run_id: str, step_id: str) -> None:
    op, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
        parent_run_id=run_id,
        step_id=step_id,
    )
    assert op.argv_snapshot is not None
    await runtime.approve(op.op_id, sha=op.argv_snapshot.sha256, by="user")
    finished = await runtime.execute(op.op_id)
    assert finished.status == OpStatus.SUCCEEDED


def test_boss_flow_step_names_and_order() -> None:
    quantitative = template_specs("quantitative")
    assert [
        (step.step_id, step.name, step.summary, step.app, step.command)
        for step in quantitative
    ] == list(_QUANT_FLOW)
    assert quantitative[1].also == (("wyx_tables", "experiment_summary"),)
    assert quantitative[-1].also[0] == ("plot_reaction_analysis", "conversion_selectivity")
    kinetics = template_specs("kinetics")
    assert [
        (step.step_id, step.name, step.summary) for step in kinetics
    ] == list(_KINETICS_FLOW)
    assert [step.required for step in kinetics] == [
        True, True, True, True, False, False, False, False, False,
    ]
    with pytest.raises(CommandOpError, match="deferred"):
        template_specs("tool:export")


def test_artifact_suffix_matches_the_file_name_only() -> None:
    decoy = Artifact(path="out/pulse_size.csv.bak", sha="a")
    real = Artifact(path="out/pulse_size.csv", sha="b")
    assert select_artifact((decoy, real), "pulse_size.csv") == real
    assert select_artifact((Artifact(path="out/not_pulse_size.csv", sha="a"),), "pulse_size.csv") is None
    assert select_artifact((decoy,), "") == decoy


@pytest.mark.asyncio
async def test_quantitative_progress_records_bindings(tmp_path: Path) -> None:
    bus = MessageBus()
    runtime = _runtime(tmp_path, bus)
    progress_events: list[TemplateRunEvent] = []

    def collect(event: TemplateRunEvent) -> None:
        progress_events.append(event)

    Workbench(runtime, bus).subscribe_progress(collect)
    run = await runtime.start_template_run("quantitative", run_id="quant-1")
    assert run.progress.done == 0
    assert run.progress.total == 7
    assert run.progress.current_step == "init_tap"
    assert run.accepted is False

    for index, step in enumerate(template_specs("quantitative"), start=1):
        await _finish_step(runtime, run.run_id, step.step_id)
        current = runtime.get_template_run(run.run_id)
        assert current.progress.done == index
        assert current.accepted is False

    finished = runtime.get_template_run(run.run_id)
    assert finished.progress.total == 7
    assert finished.progress.current_step is None
    assert run_phase(finished) == "ready_for_acceptance"
    assert {
        item.target_field: item.artifact_path for item in finished.bindings
    } == {
        "baseline_fit_artifact": "out/baseline",
        "pulse_size_csv": "out/pulse_size.csv",
        "response_areas_csv": "out/response_areas.csv",
    }
    bench = Workbench(runtime, bus)
    assert bench.suggested_params(run.run_id, "analyze_reaction") == {
        "pulse_size_csv": "out/pulse_size.csv",
        "response_areas_csv": "out/response_areas.csv",
    }
    assert bench.suggested_params(run.run_id, "process_quantitative") == {
        "baseline_fit_artifact": "out/baseline",
    }
    assert progress_events[0].name == "run.progress"
    assert progress_events[-1].done == 7
    assert progress_events[-1].status == "ready_for_acceptance"
    assert progress_events[-1].accepted is False
    assert notification_is_deliverable(
        TemplateRunEvent,
        channel="websocket",
        publish_lifecycle=True,
    ) is False

    reloaded = CommandOpRuntime(tmp_path).get_template_run(run.run_id)
    assert reloaded.progress == finished.progress
    assert reloaded.bindings == finished.bindings
    assert reloaded.steps[-1].status == "done"
    assert reloaded.accepted is False


@pytest.mark.asyncio
async def test_steps_run_in_boss_order(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    run = await runtime.start_template_run("quantitative", run_id="order-1")
    with pytest.raises(CommandOpError, match="waits on"):
        await runtime.propose(
            app="command_op_demo",
            command="write_note",
            params=_NOTE,
            parent_run_id=run.run_id,
            step_id="pulse_size",
        )
    with pytest.raises(CommandOpError, match="parent_run_id"):
        await runtime.propose(
            app="command_op_demo",
            command="write_note",
            params=_NOTE,
            step_id="init_tap",
        )
    op, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
        parent_run_id=run.run_id,
        step_id="init_tap",
    )
    with pytest.raises(CommandOpError, match="active op"):
        await runtime.propose(
            app="command_op_demo",
            command="write_note",
            params=_NOTE,
            parent_run_id=run.run_id,
            step_id="init_tap",
        )
    stored = runtime.get_template_run(run.run_id)
    assert stored.progress.done == 0
    assert stored.steps[0].op_id == op.op_id
    assert stored.steps[0].status == "current"
    assert [path.name for path in (tmp_path / "command_ops").glob("*.json")] == [f"{op.op_id}.json"]


@pytest.mark.asyncio
async def test_failed_step_stays_open_for_retry(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.register_executor("command_op_demo/write_note", _ExitCode(1))
    run = await runtime.start_template_run("quantitative", run_id="retry-1")
    op, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
        parent_run_id=run.run_id,
        step_id="init_tap",
    )
    assert op.argv_snapshot is not None
    await runtime.approve(op.op_id, sha=op.argv_snapshot.sha256, by="user")
    failed = await runtime.execute(op.op_id)
    assert failed.status == OpStatus.FAILED
    current = runtime.get_template_run(run.run_id)
    assert current.progress.done == 0
    assert current.steps[0].status == "current"
    assert current.accepted is False
    assert run_phase(current) == "active"

    runtime.register_executor("command_op_demo/write_note", _StepArtifacts())
    await _finish_step(runtime, run.run_id, "init_tap")
    retried = runtime.get_template_run(run.run_id)
    assert retried.progress.done == 1
    assert retried.progress.current_step == "wyx_summary"
    assert retried.steps[0].op_id != op.op_id


@pytest.mark.asyncio
async def test_kinetics_plots_fan_out_after_y_procedure(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    run = await runtime.start_template_run("kinetics", run_id="kin-1")
    with pytest.raises(CommandOpError, match="waits on"):
        await runtime.propose(
            app="command_op_demo",
            command="write_note",
            params=_NOTE,
            parent_run_id=run.run_id,
            step_id="y_procedure",
        )
    for step_id in ("init_tap", "wyx_summary", "pulse_size"):
        await _finish_step(runtime, run.run_id, step_id)
    suggested = Workbench(runtime).suggested_params(run.run_id, "y_procedure")
    assert suggested == {"pulse_size_csv": "out/pulse_size.csv"}
    with pytest.raises(CommandOpError, match="y_procedure"):
        await runtime.propose(
            app="command_op_demo",
            command="write_note",
            params=_NOTE,
            parent_run_id=run.run_id,
            step_id="concentrations",
        )
    await _finish_step(runtime, run.run_id, "y_procedure")
    ready = runtime.get_template_run(run.run_id)
    assert ready.progress.done == 4
    assert ready.progress.total == 9
    assert ready.progress.current_step == "concentrations"
    assert run_phase(ready) == "ready_for_acceptance"
    assert ready.accepted is False

    concentrations, _events = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
        parent_run_id=run.run_id,
        step_id="concentrations",
    )
    rates, _rates = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
        parent_run_id=run.run_id,
        step_id="rates",
    )
    branched = runtime.get_template_run(run.run_id)
    by_id = {step.step_id: step for step in branched.steps}
    assert by_id["concentrations"].op_id == concentrations.op_id
    assert by_id["rates"].op_id == rates.op_id
    assert by_id["concentrations"].status == "current"
    assert branched.progress.current_step == "rates"
    assert branched.progress.done == 4


@pytest.mark.asyncio
async def test_workbench_fill_approve_cancel_use_command_op_events(tmp_path: Path) -> None:
    bus = MessageBus()
    runtime = _runtime(tmp_path, bus)
    bench = CommandWorkbench(runtime, bus)
    seen: list[CommandOpEvent] = []

    def collect(event: CommandOpEvent) -> None:
        seen.append(event)

    bench.subscribe(collect)
    partial, _created = await runtime.propose(
        app="command_op_demo",
        command="write_note",
        params={"title": "only"},
    )
    assert bench.op_view(partial.op_id)["actions"] == {
        "fill": True,
        "approve": False,
        "cancel": True,
    }
    filled = await bench.fill(partial.op_id, _NOTE)
    view = bench.op_view(filled.op_id)
    assert view["actions"]["approve"] is True
    sha = view["argv_sha256"]
    assert isinstance(sha, str)
    assert [event.argv_sha256 for event in seen if event.name == "op.need_approval"][-1] == sha
    with pytest.raises(CommandOpError, match="sha"):
        await bench.approve(filled.op_id, sha="0" * 64, by="user")
    assert runtime.get(filled.op_id).status == OpStatus.AWAITING_APPROVAL

    approved = await bench.approve(filled.op_id, sha=sha, by="bench")
    assert approved.status == OpStatus.APPROVED
    edited = await bench.fill(approved.op_id, {**_NOTE, "title": "edited"})
    assert edited.approval is None
    assert edited.argv_snapshot is not None
    assert edited.argv_snapshot.sha256 != sha
    cancelled = await bench.cancel(edited.op_id)
    assert cancelled.status == OpStatus.CANCELLED
    assert bench.op_view(cancelled.op_id)["actions"] == {
        "fill": False,
        "approve": False,
        "cancel": False,
    }
    assert {event.name for event in seen} <= _COMMAND_OP_EVENTS
    assert "op.approved" in {event.name for event in seen}
    assert "op.cancelled" in {event.name for event in seen}
    assert "op.need_input" in {event.name for event in seen}
    assert notification_is_deliverable(
        CommandOpEvent,
        channel="websocket",
        publish_lifecycle=True,
    ) is False


@pytest.mark.asyncio
async def test_propose_tool_attaches_the_named_step(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    run = await runtime.start_template_run("quantitative", run_id="tool-1")
    result = await ProposeOpTool(runtime).execute(
        app="command_op_demo",
        command="write_note",
        params=_NOTE,
        parent_run_id=run.run_id,
        step_id="init_tap",
    )
    payload = json.loads(result)
    assert payload["step_id"] == "init_tap"
    assert payload["parent_run_id"] == "tool-1"
    assert payload["template_run"]["current_step"] == "init_tap"
    assert payload["template_run"]["done"] == 0
    assert payload["template_run"]["total"] == 7
    assert payload["cli_dispatched"] is False


@pytest.mark.asyncio
async def test_loop_workbench_approve_advances_parent_progress(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.aclose = AsyncMock()
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
    try:
        run = await loop.start_template_run("quantitative", run_id="boss-1")
        op, _events = await loop.command_ops.propose(
            app="command_op_demo",
            command="write_note",
            params=_NOTE,
            parent_run_id=run.run_id,
            step_id="init_tap",
        )
        assert op.argv_snapshot is not None
        assert loop.command_workbench().op_view(op.op_id)["actions"]["approve"] is True
        await loop.approve_command_op(op.op_id, sha=op.argv_snapshot.sha256, by="user")
        await asyncio.gather(*list(loop._background_tasks))
        assert loop.command_ops.get(op.op_id).status == OpStatus.SUCCEEDED
        progress = loop.command_ops.get_template_run(run.run_id)
        assert progress.progress.done == 1
        assert progress.progress.current_step == "wyx_summary"
        assert progress.accepted is False

        partial, _partial = await loop.command_ops.propose(
            app="command_op_demo",
            command="write_note",
            params={"title": "partial"},
            parent_run_id=run.run_id,
            step_id="wyx_summary",
        )
        filled = await loop.fill_command_op(partial.op_id, _NOTE)
        assert filled.status == OpStatus.AWAITING_APPROVAL
        cancelled = await loop.cancel_command_op(filled.op_id)
        assert cancelled.status == OpStatus.CANCELLED
        assert loop.command_ops.get_template_run(run.run_id).progress.done == 1
    finally:
        await loop.aclose()


@pytest.mark.asyncio
async def test_tap_adapter_registers_without_guessed_cli_flags(tmp_path: Path) -> None:
    runtime = CommandOpRuntime(tmp_path, auto_approve_readonly=True)
    adapter = TapAdapter(runtime)
    pending = adapter.pending("quantitative")
    assert [item.step_id for item in pending] == [
        spec.step_id for spec in template_specs("quantitative")
    ]
    pulse = next(item for item in pending if item.step_id == "pulse_size")
    assert pulse.app == "process_pulse_sizes"
    assert pulse.command == "pulse_size"
    assert pulse.red_line_ids == ("no_overwrite_wyx", "pressure_unit_match_header")
    assert pulse.describe_json_todo.startswith("TODO:")
    assert "describe-json" in pulse.describe_json_todo
    assert adapter.registered_refs == ()
    with pytest.raises(CommandOpError, match="unknown contract"):
        runtime.contract(pulse.app, pulse.command)
    kinetics = adapter.pending("kinetics")
    assert [item.step_id for item in kinetics] == [
        spec.step_id for spec in template_specs("kinetics")
    ]
    assert all(item.red_line_ids == pulse.red_line_ids for item in kinetics if item.step_id == "pulse_size")
    assert all(item.describe_json_todo.startswith("TODO:") for item in (*pending, *kinetics))

    echoed: list[tuple[str, ...]] = []

    def hook(contract: CommandContract, params: object) -> list[str]:
        del params
        echoed.append(contract.red_line_ids)
        return list(contract.red_line_ids)

    adapter.set_red_line_hook(hook)
    contract = CommandContract(
        app="tap_scaffold",
        command="noop",
        side_effects="read_only",
        red_line_ids=("no_overwrite_wyx",),
        params=(CommandParam(name="path", type="path", required=True, flag="--path"),),
    )

    class _Ran:
        async def execute(self, op: CommandOp, argv: tuple[str, ...]) -> CommandExecution:
            del op, argv
            return CommandExecution(exit_code=0, log_uri="tap-scaffold://ran")

    adapter.register_contract(contract)
    adapter.register_executor(contract.ref, _Ran())
    blocked, _blocked_events = await runtime.propose(
        app="tap_scaffold",
        command="noop",
        params={"path": "in.wyx"},
    )
    assert blocked.status == OpStatus.NEED_INPUT
    assert blocked.validation_errors == ("no_overwrite_wyx",)
    assert echoed == [("no_overwrite_wyx",)]

    def allow(contract: CommandContract, params: object) -> list[str]:
        del contract, params
        return []

    adapter.set_red_line_hook(allow)
    op, _events = await runtime.propose(
        app="tap_scaffold",
        command="noop",
        params={"path": "in.wyx"},
    )
    assert op.status == OpStatus.APPROVED
    finished = await runtime.execute(op.op_id)
    assert finished.run is not None
    assert finished.run.log_uri == "tap-scaffold://ran"
    assert adapter.registered_refs == ("tap_scaffold/noop",)
    with pytest.raises(CommandOpError, match="unknown contract"):
        runtime.contract("process_pulse_sizes", "pulse_size")


@pytest.mark.asyncio
async def test_reordered_template_file_is_rejected(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    run = await runtime.start_template_run("quantitative", run_id="file-1")
    path = runtime.template_runs.path_for(run.run_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["steps"][0]["step_id"] = "pulse_size"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CommandOpError, match="boss flow"):
        runtime.get_template_run(run.run_id)
