"""Describe-json catalog loading and the taptap subprocess executor."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.command_op.describe import (
    DESCRIBE_SCHEMA,
    CatalogDiscovery,
    discover_taptap_catalog,
    parse_describe_document,
)
from nanobot.agent.command_op.executor import TapTapExecutor
from nanobot.agent.command_op.models import Artifact, CommandOpError, OpStatus
from nanobot.agent.command_op.runtime import CommandOpRuntime
from nanobot.agent.command_op.tap_adapter import TapAdapter
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus

_FIXTURE = Path(__file__).parent / "fixtures" / "taptap_describe_sample.json"


def _document() -> dict[str, object]:
    loaded = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.aclose = AsyncMock()
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )


def test_sample_fixture_is_not_marked_as_a_live_export() -> None:
    document = _document()
    assert document["schema"] == DESCRIBE_SCHEMA
    note = document["note"]
    assert isinstance(note, str)
    assert "Not a live" in note
    parsed = parse_describe_document(document)
    assert "process_pulse_sizes/pulse_size" in {item.ref for item in parsed}
    assert "process_reaction_analysis/analyze_reaction" not in {item.ref for item in parsed}


def test_parser_rejects_a_param_without_a_flag_or_positional() -> None:
    document = _document()
    commands = document["commands"]
    assert isinstance(commands, list)
    broken = json.loads(json.dumps(document))
    broken["commands"] = [dict(commands[0])]
    broken["commands"][0]["params"] = [{"name": "output", "type": "path", "required": True}]
    with pytest.raises(CommandOpError, match="flag or positional"):
        parse_describe_document(broken)


def test_discovery_reports_unavailable_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def invoke(argv: list[str]) -> tuple[int, str, str]:
        nonlocal called
        called = True
        return 0, "", ""

    discovery = discover_taptap_catalog(
        lookup=lambda _name: None,
        module_present=lambda: False,
        invoke=invoke,
    )
    assert discovery.status == "unavailable"
    assert discovery.document is None
    assert "describe-json" in discovery.detail
    assert called is False
    monkeypatch.undo()


def test_discovery_parses_live_stdout() -> None:
    document = _document()

    def invoke(argv: list[str]) -> tuple[int, str, str]:
        assert argv == ["/usr/bin/taptap", "--describe-json"]
        return 0, json.dumps(document), ""

    discovery = discover_taptap_catalog(
        lookup=lambda name: "/usr/bin/taptap" if name == "taptap" else None,
        module_present=lambda: False,
        invoke=invoke,
    )
    assert discovery.status == "live"
    assert discovery.detail == "taptap --describe-json"
    assert parse_describe_document(discovery.document) == parse_describe_document(document)


def test_invalid_describe_output_registers_nothing() -> None:
    discovery = discover_taptap_catalog(
        lookup=lambda name: "/usr/bin/taptap" if name == "taptap" else None,
        module_present=lambda: False,
        invoke=lambda _argv: (0, '{"schema":"other"}', ""),
    )
    assert discovery.status == "invalid"
    assert discovery.document is None


@pytest.mark.asyncio
async def test_describe_document_registers_executor_without_touching_red_line_hook(
    tmp_path: Path,
) -> None:
    runtime = CommandOpRuntime(tmp_path, auto_approve_readonly=False)
    adapter = TapAdapter(runtime)
    seen: list[str] = []

    def hook(contract: object, params: object) -> list[str]:
        del params
        seen.append("hook")
        assert getattr(contract, "red_line_ids") == (
            "no_overwrite_wyx",
            "pressure_unit_match_header",
        )
        return ["from-test"]

    adapter.set_red_line_hook(hook)
    document = _document()
    refs = adapter.load_describe_document(document, TapTapExecutor(tmp_path))
    assert "process_pulse_sizes/pulse_size" in refs
    parsed = {item.ref: item for item in parse_describe_document(document)}
    assert runtime.contract("process_pulse_sizes", "pulse_size") == parsed[
        "process_pulse_sizes/pulse_size"
    ]
    pulse = next(item for item in adapter.pending("quantitative") if item.step_id == "pulse_size")
    assert pulse.describe_json_todo == ""
    assert pulse.catalog_source == "describe-json"
    waiting = next(item for item in adapter.pending("quantitative") if item.step_id == "fit_baseline")
    assert waiting.describe_json_todo.startswith("TODO:")
    assert waiting.catalog_source is None
    with pytest.raises(CommandOpError, match="unknown contract"):
        runtime.contract("process_reaction_analysis", "analyze_reaction")

    blocked, _events = await runtime.propose(
        app="process_pulse_sizes",
        command="pulse_size",
        params={},
    )
    assert blocked.status == OpStatus.NEED_INPUT
    assert "from-test" in blocked.validation_errors
    assert seen == ["hook"]


@pytest.mark.asyncio
async def test_executor_records_exit_log_and_artifact(tmp_path: Path) -> None:
    script = (
        "import pathlib,sys; args=sys.argv; i=args.index('--output'); "
        "pathlib.Path(args[i+1]).write_text('ran\\n', encoding='utf-8')"
    )
    document = {
        "schema": DESCRIBE_SCHEMA,
        "executable": [sys.executable, "-c", script],
        "commands": [{
            "app": "sample_app",
            "command": "write_file",
            "side_effects": "writes_new",
            "red_line_ids": ["opaque"],
            "params": [
                {"name": "output", "type": "path", "required": True, "flag": "--output"},
                {"name": "note", "type": "string", "required": True, "flag": "--note"},
            ],
        }],
    }
    runtime = CommandOpRuntime(tmp_path, auto_approve_readonly=False)
    adapter = TapAdapter(runtime)
    adapter.load_describe_document(document, TapTapExecutor(tmp_path))
    op, _events = await runtime.propose(
        app="sample_app",
        command="write_file",
        params={"output": "out.txt", "note": "/etc/passwd"},
    )
    assert op.status == OpStatus.AWAITING_APPROVAL
    assert op.argv_snapshot is not None
    approved, _approved = await runtime.approve(op.op_id, sha=op.argv_snapshot.sha256, by="user")
    finished = await runtime.execute(approved.op_id)
    assert finished.status == OpStatus.SUCCEEDED
    assert finished.run is not None
    assert finished.run.exit_code == 0
    assert finished.run.log_uri == f"command_ops/logs/{op.op_id}.log"
    log_text = (tmp_path / finished.run.log_uri).read_text(encoding="utf-8")
    assert log_text.startswith("argv: " + json.dumps(list(op.argv_snapshot.argv), ensure_ascii=False))
    written = tmp_path / "out.txt"
    assert written.read_text(encoding="utf-8") == "ran\n"
    assert finished.artifacts == (
        Artifact(path="out.txt", sha=hashlib.sha256(b"ran\n").hexdigest()),
    )
    assert all(not item.path.startswith("/") for item in finished.artifacts)
    assert all("passwd" not in item.path for item in finished.artifacts)


@pytest.mark.asyncio
async def test_executor_keeps_nonzero_exit_on_the_op(tmp_path: Path) -> None:
    document = {
        "schema": DESCRIBE_SCHEMA,
        "executable": [sys.executable, "-c", "raise SystemExit(3)"],
        "commands": [{
            "app": "sample_app",
            "command": "fail",
            "side_effects": "writes_new",
            "params": [
                {"name": "label", "type": "string", "required": True, "flag": "--label"},
            ],
        }],
    }
    runtime = CommandOpRuntime(tmp_path, auto_approve_readonly=False)
    TapAdapter(runtime).load_describe_document(document, TapTapExecutor(tmp_path))
    op, _events = await runtime.propose(
        app="sample_app",
        command="fail",
        params={"label": "nope"},
    )
    assert op.argv_snapshot is not None
    await runtime.approve(op.op_id, sha=op.argv_snapshot.sha256, by="user")
    finished = await runtime.execute(op.op_id)
    assert finished.status == OpStatus.FAILED
    assert finished.run is not None
    assert finished.run.exit_code == 3
    assert finished.run.log_uri is not None
    assert (tmp_path / finished.run.log_uri).is_file()


@pytest.mark.asyncio
async def test_loop_leaves_tap_unregistered_when_describe_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nanobot.agent.command_op.describe.discover_taptap_catalog",
        lambda: CatalogDiscovery(
            status="unavailable",
            detail="taptap --describe-json is not available on this machine",
        ),
    )
    loop = _loop(tmp_path)
    try:
        assert loop.command_ops.catalog_status == "unavailable"
        assert "describe-json" in loop.command_ops.catalog_detail
        with pytest.raises(CommandOpError, match="unknown contract"):
            loop.command_ops.contract("process_pulse_sizes", "pulse_size")
        pending = loop.tap_adapter.pending("quantitative")
        assert all(item.describe_json_todo.startswith("TODO:") for item in pending)
    finally:
        await loop.aclose()


@pytest.mark.asyncio
async def test_loop_registers_live_describe_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()

    def discover() -> CatalogDiscovery:
        return CatalogDiscovery(status="live", detail="taptap --describe-json", document=document)

    monkeypatch.setattr(
        "nanobot.agent.command_op.describe.discover_taptap_catalog",
        discover,
    )
    loop = _loop(tmp_path)
    try:
        assert loop.command_ops.catalog_status == "live"
        parsed = {item.ref: item for item in parse_describe_document(document)}
        assert loop.command_ops.contract("wyx_inspect", "wyx_summary") == parsed[
            "wyx_inspect/wyx_summary"
        ]
        pulse = next(item for item in loop.tap_adapter.pending("kinetics") if item.step_id == "pulse_size")
        assert pulse.catalog_source == "describe-json"
    finally:
        await loop.aclose()
