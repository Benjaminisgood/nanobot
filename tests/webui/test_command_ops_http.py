"""WebUI command-op routes call the workbench with the approval sha."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.command_op.describe import CatalogDiscovery
from nanobot.agent.command_op.events import CommandOpEvent
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.channels.websocket.runtime import WebSocketConfig
from nanobot.webui.client_contract import COMMAND_OPS_CAPABILITY, webui_contract
from nanobot.webui.command_ops_api import command_op_wire
from nanobot.webui.gateway_services import build_gateway_services

_NOTE = {"title": "baseline", "body": "peek", "output_dir": "notes"}


def _loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentLoop:
    monkeypatch.setattr(
        "nanobot.agent.command_op.describe.discover_taptap_catalog",
        lambda: CatalogDiscovery(
            status="unavailable",
            detail="taptap --describe-json is not available on this machine",
        ),
    )
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.aclose = AsyncMock()
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )


@pytest.mark.asyncio
async def test_approve_route_requires_the_need_approval_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _loop(tmp_path, monkeypatch)
    try:
        from nanobot.webui.command_ops_api import dispatch_command_ops

        async def action(name: str, payload: dict[str, object]) -> dict[str, object]:
            return await dispatch_command_ops(loop, name, payload)

        services = build_gateway_services(
            config=WebSocketConfig(),
            bus=loop.bus,
            session_manager=None,
            static_dist_path=None,
            workspace_path=tmp_path,
            default_restrict_to_workspace=True,
            runtime_model_name=lambda: "test-model",
            runtime_surface="browser",
            runtime_capabilities_overrides=None,
            command_ops_action=action,
        )
        http = services.http
        anonymous = _Request()
        denied = await http._handle_command_ops_read(anonymous)
        assert denied.status_code == 401

        op, events = await loop.command_ops.propose(
            app="command_op_demo",
            command="write_note",
            params=_NOTE,
        )
        assert op.argv_snapshot is not None
        approval = next(event for event in events if event.name == "op.need_approval")
        wire = command_op_wire(approval)
        assert wire["argv_sha256"] == op.argv_snapshot.sha256
        assert wire["event"] == "command_op"

        trusted = _Request()
        setattr(trusted, "_nanobot_trusted_proxy_authenticated", True)
        listed = await http._handle_command_ops_read(trusted)
        assert listed.status_code == 200
        assert op.op_id.encode() in listed.body

        mismatch = await http.dispatch_webui_mutation(
            SimpleNamespace(request=None),
            "command_op.approve",
            {"op_id": op.op_id, "sha": "0" * 64},
        )
        assert mismatch.status_code == 400
        assert loop.command_ops.get(op.op_id).status.value == "awaiting_approval"

        missing = await http.dispatch_webui_mutation(
            SimpleNamespace(request=None),
            "command_op.approve",
            {"op_id": op.op_id},
        )
        assert missing.status_code == 400

        approved = await http.dispatch_webui_mutation(
            SimpleNamespace(request=None),
            "command_op.approve",
            {"op_id": op.op_id, "sha": approval.argv_sha256},
        )
        assert approved.status_code == 200
        assert isinstance(approval.argv_sha256, str)
        assert approval.argv_sha256 == op.argv_snapshot.sha256
        await _wait(loop)
        assert loop.command_ops.get(op.op_id).status.value == "succeeded"
        assert COMMAND_OPS_CAPABILITY in webui_contract()["capabilities"]
    finally:
        await loop.aclose()


def test_command_op_wire_carries_need_approval_sha() -> None:
    event = CommandOpEvent(
        name="op.need_approval",
        op_id="op-1",
        status="awaiting_approval",
        blocked_reason="need_approval",
        contract_ref="command_op_demo/write_note",
        argv_sha256="abc",
        parent_run_id="run-1",
        step_id="init_tap",
    )
    assert command_op_wire(event)["argv_sha256"] == "abc"


class _Request:
    path = "/api/webui/command-ops"
    headers: dict[str, str] = {}


async def _wait(loop: AgentLoop) -> None:
    import asyncio

    tasks = list(loop._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)
