"""Parent TemplateRun: ordered boss-flow steps, progress, and artifact bindings.

Step ids and order follow the quantitative and kinetics charts. The kernel
stores binding results. It does not interpret TAP science, and a zero exit
code does not mark the run scientifically accepted.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from nanobot.agent.command_op.events import TemplateRunEvent
from nanobot.agent.command_op.models import Artifact, CommandOp, CommandOpError, OpStatus
from nanobot.utils.helpers import atomic_write_text

StepStatus = Literal["pending", "current", "done"]
RunPhase = Literal["active", "ready_for_acceptance"]

# Opaque ids copied from the template-map schema sketch. This module does not
# check them; a TAP adapter may pass them through ``set_red_line_hook``.
_PULSE_RED_LINES = ("no_overwrite_wyx", "pressure_unit_match_header")


@dataclass(frozen=True)
class StepSpec:
    """One boss-flow step. ``also`` lists other CLIs named for the same step."""

    step_id: str
    name: str
    summary: str
    app: str
    command: str
    requires: tuple[str, ...] = ()
    required: bool = True
    red_line_ids: tuple[str, ...] = ()
    also: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class BindingRule:
    """Declarative artifact edge. An empty suffix binds the executor's first artifact.

    The kernel does not choose among response, baseline, and total tables.
    The executor registers the path that should fill ``target_field``.
    """

    source_step: str
    artifact_suffix: str
    target_step: str
    target_field: str


@dataclass(frozen=True)
class RunStep:
    step_id: str
    name: str
    summary: str
    app: str
    command: str
    required: bool
    op_id: str | None = None
    status: StepStatus = "pending"


@dataclass(frozen=True)
class AppliedBinding:
    """A stored binding result: one artifact path copied onto a later step's field."""

    source_step: str
    source_op_id: str
    artifact_path: str
    artifact_sha: str
    target_step: str
    target_field: str


@dataclass(frozen=True)
class TemplateProgress:
    done: int
    total: int
    current_step: str | None


@dataclass(frozen=True)
class TemplateRun:
    run_id: str
    template: str
    steps: tuple[RunStep, ...]
    progress: TemplateProgress
    bindings: tuple[AppliedBinding, ...] = ()
    accepted: bool = False


# Quantitative order is the section-二 chart. ``name`` is the node title;
# ``summary`` is the 老板一句话 column.
QUANTITATIVE_STEPS: tuple[StepSpec, ...] = (
    StepSpec(
        step_id="init_tap",
        name="项目就绪",
        summary="有没有配置",
        app="project_config",
        command="init_tap",
    ),
    StepSpec(
        step_id="wyx_summary",
        name="抽查 WYX",
        summary="数据能不能用",
        app="wyx_inspect",
        command="wyx_summary",
        requires=("init_tap",),
        also=(("wyx_tables", "experiment_summary"),),
    ),
    StepSpec(
        step_id="fit_baseline",
        name="基线拟合",
        summary="基线漂多少",
        app="fit_baseline_levels",
        command="fit_baseline",
        requires=("wyx_summary",),
    ),
    StepSpec(
        step_id="pulse_size",
        name="脉冲量",
        summary="每脉冲多少 mol",
        app="process_pulse_sizes",
        command="pulse_size",
        requires=("fit_baseline",),
        red_line_ids=_PULSE_RED_LINES,
    ),
    StepSpec(
        step_id="process_quantitative",
        name="定量面积",
        summary="峰面积",
        app="process_quantitative_areas",
        command="process_quantitative",
        requires=("pulse_size",),
    ),
    StepSpec(
        step_id="analyze_reaction",
        name="反应核算",
        summary="转化 / 选择 / 元素收支",
        app="process_reaction_analysis",
        command="analyze_reaction",
        requires=("process_quantitative",),
    ),
    StepSpec(
        step_id="product_distributions",
        name="出图",
        summary="标准图",
        app="plot_reaction_analysis",
        command="product_distributions",
        requires=("analyze_reaction",),
        also=(
            ("plot_reaction_analysis", "conversion_selectivity"),
            ("plot_reaction_analysis", "element_balances"),
            ("quantitative_area_plots", "plot_quantitative_areas"),
        ),
    ),
)

# Kinetics 0–1 are the quantitative steps (table: 同定量 / 同上), not one merged
# node. Plots 4a–e are optional and each wait only on Y-procedure.
KINETICS_STEPS: tuple[StepSpec, ...] = (
    StepSpec(
        step_id="init_tap",
        name="项目就绪",
        summary="有没有配置",
        app="project_config",
        command="init_tap",
    ),
    StepSpec(
        step_id="wyx_summary",
        name="抽查 WYX",
        summary="数据能不能用",
        app="wyx_inspect",
        command="wyx_summary",
        requires=("init_tap",),
        also=(("wyx_tables", "experiment_summary"),),
    ),
    StepSpec(
        step_id="pulse_size",
        name="脉冲量",
        summary="脉冲量（表或固定值，二选一）",
        app="process_pulse_sizes",
        command="pulse_size",
        requires=("wyx_summary",),
        red_line_ids=_PULSE_RED_LINES,
    ),
    StepSpec(
        step_id="y_procedure",
        name="Y-procedure",
        summary="出口流 → 浓度/速率",
        app="process_y_procedure",
        command="y_procedure",
        requires=("pulse_size",),
    ),
    StepSpec(
        step_id="concentrations",
        name="浓度图",
        summary="出图包（可多选）",
        app="plot_y_procedure_concentrations",
        command="concentrations",
        requires=("y_procedure",),
        required=False,
    ),
    StepSpec(
        step_id="rates",
        name="速率图",
        summary="出图包（可多选）",
        app="plot_y_procedure_rates",
        command="rates",
        requires=("y_procedure",),
        required=False,
    ),
    StepSpec(
        step_id="rate_concentration_correlations",
        name="rate–c 相关",
        summary="出图包（可多选）",
        app="plot_y_procedure_rate_concentration_correlations",
        command="rate_concentration_correlations",
        requires=("y_procedure",),
        required=False,
    ),
    StepSpec(
        step_id="accumulation_balances",
        name="累积收支",
        summary="出图包（可多选）",
        app="plot_y_procedure_accumulation_balances",
        command="accumulation_balances",
        requires=("y_procedure",),
        required=False,
    ),
    StepSpec(
        step_id="filter_review",
        name="滤波复核",
        summary="出图包（可多选）",
        app="plot_y_procedure_filter_review",
        command="filter_review",
        requires=("y_procedure",),
        required=False,
    ),
)

# Dotted edges on the quantitative chart, plus the kinetics pulse table feeding
# Y-procedure. ``fixed_pulse_size_mol`` is an exclusive input, not an artifact.
QUANTITATIVE_BINDINGS: tuple[BindingRule, ...] = (
    BindingRule("fit_baseline", "", "process_quantitative", "baseline_fit_artifact"),
    BindingRule("pulse_size", "pulse_size.csv", "analyze_reaction", "pulse_size_csv"),
    BindingRule("process_quantitative", "", "analyze_reaction", "response_areas_csv"),
)

KINETICS_BINDINGS: tuple[BindingRule, ...] = (
    BindingRule("pulse_size", "pulse_size.csv", "y_procedure", "pulse_size_csv"),
)


def template_specs(template: str) -> tuple[StepSpec, ...]:
    if template == "quantitative":
        return QUANTITATIVE_STEPS
    if template == "kinetics":
        return KINETICS_STEPS
    if template.startswith("tool:"):
        raise CommandOpError("tool templates are deferred")
    raise CommandOpError(f"unknown template {template}")


def binding_rules(template: str) -> tuple[BindingRule, ...]:
    if template == "quantitative":
        return QUANTITATIVE_BINDINGS
    if template == "kinetics":
        return KINETICS_BINDINGS
    raise CommandOpError(f"unknown template {template}")


def spec_for(template: str, step_id: str) -> StepSpec:
    for spec in template_specs(template):
        if spec.step_id == step_id:
            return spec
    raise CommandOpError(f"unknown template step {step_id}")


def recompute(steps: Sequence[RunStep], *, prefer: str | None) -> TemplateProgress:
    done = sum(1 for step in steps if step.status == "done")
    incomplete = [step.step_id for step in steps if step.status != "done"]
    if prefer is not None and prefer in incomplete:
        current: str | None = prefer
    elif incomplete:
        current = incomplete[0]
    else:
        current = None
    return TemplateProgress(done=done, total=len(steps), current_step=current)


def run_phase(run: TemplateRun) -> RunPhase:
    """Main-line completion. This is 待科学验收, not the acceptance checkbox."""
    required_done = all(step.status == "done" for step in run.steps if step.required)
    if required_done:
        return "ready_for_acceptance"
    return "active"


def new_template_run(template: str, *, run_id: str) -> TemplateRun:
    if not run_id:
        raise CommandOpError("invalid template run id")
    steps = tuple(_run_step(spec) for spec in template_specs(template))
    return TemplateRun(
        run_id=run_id,
        template=template,
        steps=steps,
        progress=recompute(steps, prefer=None),
    )


def attach_step(
    run: TemplateRun,
    step_id: str,
    op_id: str,
    *,
    existing_status: OpStatus | None,
) -> TemplateRun:
    """Bind a child op to a step. Earlier required steps must already be done."""
    spec = spec_for(run.template, step_id)
    by_id = {step.step_id: step for step in run.steps}
    if step_id not in by_id:
        raise CommandOpError(f"unknown template step {step_id}")
    for required_id in spec.requires:
        if by_id[required_id].status != "done":
            raise CommandOpError(f"step {step_id} waits on {required_id}")
    if existing_status not in (None, OpStatus.FAILED, OpStatus.CANCELLED):
        raise CommandOpError(f"step {step_id} already has an active op")
    steps = tuple(
        _replace_step(step, op_id) if step.step_id == step_id else step
        for step in run.steps
    )
    return _replace_run(run, steps, run.bindings, prefer=step_id)


def note_op_finished(run: TemplateRun, op: CommandOp) -> TemplateRun:
    """Advance progress from a child op. Exit code 0 does not accept the run."""
    if op.step_id is None:
        return run
    matched = False
    steps: list[RunStep] = []
    for step in run.steps:
        if step.step_id != op.step_id:
            steps.append(step)
            continue
        matched = True
        if step.op_id != op.op_id:
            steps.append(step)
            continue
        if op.status == OpStatus.SUCCEEDED:
            steps.append(_with_status(step, "done"))
        else:
            steps.append(_with_status(step, "current"))
    if not matched:
        raise CommandOpError(f"unknown template step {op.step_id}")
    new_steps = tuple(steps)
    bindings = run.bindings
    prefer: str | None = op.step_id
    if op.status == OpStatus.SUCCEEDED:
        bindings = apply_bindings(run, op)
        prefer = None
    return _replace_run(run, new_steps, bindings, prefer=prefer)


def apply_bindings(run: TemplateRun, op: CommandOp) -> tuple[AppliedBinding, ...]:
    kept = tuple(item for item in run.bindings if item.source_step != op.step_id)
    if op.step_id is None:
        return kept
    added: list[AppliedBinding] = []
    for rule in binding_rules(run.template):
        if rule.source_step != op.step_id:
            continue
        artifact = select_artifact(op.artifacts, rule.artifact_suffix)
        if artifact is None:
            continue
        added.append(AppliedBinding(
            source_step=op.step_id,
            source_op_id=op.op_id,
            artifact_path=artifact.path,
            artifact_sha=artifact.sha,
            target_step=rule.target_step,
            target_field=rule.target_field,
        ))
    return kept + tuple(added)


def select_artifact(artifacts: Sequence[Artifact], suffix: str) -> Artifact | None:
    if suffix == "":
        return artifacts[0] if artifacts else None
    for artifact in artifacts:
        if _suffix_match(artifact.path, suffix):
            return artifact
    return None


def template_run_event(run: TemplateRun) -> TemplateRunEvent:
    progress = run.progress
    return TemplateRunEvent(
        name="run.progress",
        run_id=run.run_id,
        template=run.template,
        done=progress.done,
        total=progress.total,
        current_step=progress.current_step,
        status=run_phase(run),
        accepted=run.accepted,
    )


def template_run_to_dict(run: TemplateRun) -> dict[str, Any]:
    progress = run.progress
    return {
        "run_id": run.run_id,
        "template": run.template,
        "status": run_phase(run),
        "accepted": run.accepted,
        "progress": {
            "done": progress.done,
            "total": progress.total,
            "current_step": progress.current_step,
        },
        "steps": [
            {
                "step_id": step.step_id,
                "name": step.name,
                "summary": step.summary,
                "app": step.app,
                "command": step.command,
                "required": step.required,
                "op_id": step.op_id,
                "status": step.status,
            }
            for step in run.steps
        ],
        "bindings": [
            {
                "source_step": item.source_step,
                "source_op_id": item.source_op_id,
                "artifact_path": item.artifact_path,
                "artifact_sha": item.artifact_sha,
                "target_step": item.target_step,
                "target_field": item.target_field,
            }
            for item in run.bindings
        ],
    }


def template_run_from_dict(data: object) -> TemplateRun:
    """Parse a persisted run. Step ids must still match the boss-flow order."""
    record = _object(data, "template run")
    template = _require_str(record, "template")
    specs = template_specs(template)
    steps = _load_steps(specs, record.get("steps"))
    accepted = record.get("accepted")
    if not isinstance(accepted, bool):
        raise CommandOpError("template run accepted must be a boolean")
    prefer = _stored_current(record.get("progress"))
    return TemplateRun(
        run_id=_require_str(record, "run_id"),
        template=template,
        steps=steps,
        progress=recompute(steps, prefer=prefer),
        bindings=_load_bindings(record.get("bindings")),
        accepted=accepted,
    )


class TemplateRunStore:
    """Durable parent runs. One JSON file per run, replaced atomically."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, run_id: str) -> Path:
        if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
            raise CommandOpError("invalid template run id")
        return self.root / f"{run_id}.json"

    def save(self, run: TemplateRun) -> None:
        payload = template_run_to_dict(run)
        atomic_write_text(
            self.path_for(run.run_id),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def find(self, run_id: str) -> TemplateRun | None:
        path = self.path_for(run_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandOpError(f"corrupt template run {run_id}") from exc
        return template_run_from_dict(data)


def _run_step(spec: StepSpec) -> RunStep:
    return RunStep(
        step_id=spec.step_id,
        name=spec.name,
        summary=spec.summary,
        app=spec.app,
        command=spec.command,
        required=spec.required,
    )


def _replace_step(step: RunStep, op_id: str) -> RunStep:
    return RunStep(
        step_id=step.step_id,
        name=step.name,
        summary=step.summary,
        app=step.app,
        command=step.command,
        required=step.required,
        op_id=op_id,
        status="current",
    )


def _with_status(step: RunStep, status: StepStatus) -> RunStep:
    return RunStep(
        step_id=step.step_id,
        name=step.name,
        summary=step.summary,
        app=step.app,
        command=step.command,
        required=step.required,
        op_id=step.op_id,
        status=status,
    )


def _replace_run(
    run: TemplateRun,
    steps: tuple[RunStep, ...],
    bindings: tuple[AppliedBinding, ...],
    *,
    prefer: str | None,
) -> TemplateRun:
    return TemplateRun(
        run_id=run.run_id,
        template=run.template,
        steps=steps,
        progress=recompute(steps, prefer=prefer),
        bindings=bindings,
        accepted=run.accepted,
    )


def _suffix_match(path: str, suffix: str) -> bool:
    return path == suffix or path.endswith("/" + suffix) or path.endswith("\\" + suffix)


def _load_steps(specs: tuple[StepSpec, ...], value: object) -> tuple[RunStep, ...]:
    if not isinstance(value, list):
        raise CommandOpError("template run steps do not match the boss flow")
    entries = cast(list[object], value)
    if len(entries) != len(specs):
        raise CommandOpError("template run steps do not match the boss flow")
    loaded: list[RunStep] = []
    for spec, entry in zip(specs, entries, strict=True):
        record = _object(entry, "template step")
        if record.get("step_id") != spec.step_id:
            raise CommandOpError("template run steps do not match the boss flow")
        status = record.get("status")
        if status not in {"pending", "current", "done"}:
            raise CommandOpError(f"unknown template step status {status!r}")
        loaded.append(RunStep(
            step_id=spec.step_id,
            name=spec.name,
            summary=spec.summary,
            app=spec.app,
            command=spec.command,
            required=spec.required,
            op_id=_optional_str(record.get("op_id"), "op_id"),
            status=cast(StepStatus, status),
        ))
    return tuple(loaded)


def _load_bindings(value: object) -> tuple[AppliedBinding, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CommandOpError("template run bindings must be a list")
    items: list[AppliedBinding] = []
    for entry in cast(list[object], value):
        record = _object(entry, "binding")
        items.append(AppliedBinding(
            source_step=_require_str(record, "source_step"),
            source_op_id=_require_str(record, "source_op_id"),
            artifact_path=_require_str(record, "artifact_path"),
            artifact_sha=_require_str(record, "artifact_sha"),
            target_step=_require_str(record, "target_step"),
            target_field=_require_str(record, "target_field"),
        ))
    return tuple(items)


def _stored_current(value: object) -> str | None:
    if value is None:
        return None
    record = _object(value, "progress")
    current = record.get("current_step")
    if current is None:
        return None
    if isinstance(current, str) and current:
        return current
    raise CommandOpError("progress.current_step must be a string or null")


def _object(data: object, label: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CommandOpError(f"{label} must be an object")
    parsed: dict[str, Any] = {}
    for key, item in cast(dict[object, object], data).items():
        if not isinstance(key, str):
            raise CommandOpError(f"{label} keys must be strings")
        parsed[key] = item
    return parsed


def _require_str(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise CommandOpError(f"template run field {key} must be a string")
    return value


def _optional_str(value: object, key: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    raise CommandOpError(f"template run field {key} must be a string or null")
