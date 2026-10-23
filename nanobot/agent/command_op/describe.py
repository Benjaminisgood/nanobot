"""Load CommandContracts from ``taptap --describe-json``.

The accepted document is ``schema: taptap.describe/v1``. Flag and positional
names are taken from that document only. A missing binary is reported as
``unavailable`` and registers nothing. A document that does not match the
schema is ``invalid`` and also registers nothing.

``tests/agent/fixtures/taptap_describe_sample.json`` is a sample of this
schema for the loader. It is not a live ``taptap`` export.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from nanobot.agent.command_op.models import (
    CommandContract,
    CommandOpError,
    CommandParam,
    ParamType,
    SideEffect,
    validate_contract,
)
from nanobot.agent.command_op.tap_adapter import TapAdapter

DESCRIBE_SCHEMA = "taptap.describe/v1"
CatalogStatus = Literal["live", "unavailable", "invalid"]

_SIDE_EFFECTS = frozenset({"read_only", "writes_new", "mutates", "instrument"})
_PARAM_TYPES = frozenset({"string", "integer", "number", "boolean", "path"})


@dataclass(frozen=True)
class CatalogDiscovery:
    """What a runtime probe of ``taptap --describe-json`` found."""

    status: CatalogStatus
    detail: str
    document: object | None = None


def parse_describe_document(data: object) -> tuple[CommandContract, ...]:
    """Parse one describe-json document into contracts. Unknown commands are omitted by absence."""
    record = _object(data, "describe-json")
    schema = record.get("schema")
    if schema != DESCRIBE_SCHEMA:
        raise CommandOpError(f"describe-json schema must be {DESCRIBE_SCHEMA}")
    executable = _strings(record.get("executable"), "executable")
    if not executable:
        raise CommandOpError("describe-json executable is required")
    raw_commands = record.get("commands")
    if not isinstance(raw_commands, list) or not raw_commands:
        raise CommandOpError("describe-json commands must be a non-empty list")
    contracts: list[CommandContract] = []
    seen: set[str] = set()
    for item in cast(list[object], raw_commands):
        contract = _contract(item, executable)
        if contract.ref in seen:
            raise CommandOpError(f"duplicate command {contract.ref}")
        seen.add(contract.ref)
        validate_contract(contract)
        contracts.append(contract)
    return tuple(contracts)


def install_live_catalog(
    adapter: TapAdapter,
    workspace: Path,
    discovery: CatalogDiscovery,
) -> tuple[str, ...]:
    """Register contracts when discovery returned a live document.

    No red-line hook is installed. Ids on the document stay opaque.
    """
    if discovery.status != "live" or discovery.document is None:
        return ()
    from nanobot.agent.command_op.executor import TapTapExecutor

    return adapter.load_describe_document(discovery.document, TapTapExecutor(workspace))


def discover_taptap_catalog(
    *,
    lookup: Callable[[str], str | None] | None = None,
    module_present: Callable[[], bool] | None = None,
    invoke: Callable[[list[str]], tuple[int, str, str]] | None = None,
) -> CatalogDiscovery:
    """Run ``taptap --describe-json`` when the CLI is installed.

    ``python -m taptap --describe-json`` is used only when the ``taptap`` module
    is installed and the binary is not on ``PATH``. Neither probe is attempted
    when both are missing.
    """
    find = lookup or shutil.which
    present = module_present or _taptap_module_present
    binary = find("taptap")
    if binary:
        argv = [binary, "--describe-json"]
    elif present():
        argv = [sys.executable, "-m", "taptap", "--describe-json"]
    else:
        return CatalogDiscovery(
            status="unavailable",
            detail="taptap --describe-json is not available on this machine",
        )
    try:
        code, stdout, stderr = (invoke or _invoke)(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CatalogDiscovery(status="unavailable", detail=str(exc))
    if code != 0:
        detail = stderr.strip() or stdout.strip() or "taptap --describe-json failed"
        return CatalogDiscovery(status="unavailable", detail=detail)
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError:
        return CatalogDiscovery(
            status="invalid",
            detail="taptap --describe-json did not return JSON",
        )
    try:
        parse_describe_document(document)
    except CommandOpError as exc:
        return CatalogDiscovery(status="invalid", detail=exc.message)
    return CatalogDiscovery(
        status="live",
        detail="taptap --describe-json",
        document=document,
    )


def _contract(data: object, executable: tuple[str, ...]) -> CommandContract:
    record = _object(data, "command")
    app = _text(record.get("app"), "app")
    command = _text(record.get("command"), "command")
    side_effects = record.get("side_effects")
    if side_effects not in _SIDE_EFFECTS:
        raise CommandOpError(f"{app}/{command} has invalid side_effects")
    raw_params = record.get("params", [])
    if not isinstance(raw_params, list):
        raise CommandOpError(f"{app}/{command} params must be a list")
    params = tuple(_param(item) for item in cast(list[object], raw_params))
    return CommandContract(
        app=app,
        command=command,
        side_effects=cast(SideEffect, side_effects),
        params=params,
        red_line_ids=_optional_strings(record.get("red_line_ids"), "red_line_ids"),
        executable=executable,
    )


def _param(data: object) -> CommandParam:
    record = _object(data, "param")
    name = _text(record.get("name"), "param name")
    raw_type = record.get("type")
    if raw_type not in _PARAM_TYPES:
        raise CommandOpError(f"{name} has unsupported type {raw_type!r}")
    required = record.get("required", False)
    if not isinstance(required, bool):
        raise CommandOpError(f"{name} required must be a boolean")
    flag = record.get("flag")
    positional = record.get("positional")
    if flag is not None and not isinstance(flag, str):
        raise CommandOpError(f"{name} flag must be a string")
    if positional is not None and (isinstance(positional, bool) or not isinstance(positional, int)):
        raise CommandOpError(f"{name} positional must be an integer")
    enum = _optional_enum(record.get("enum"), name)
    return CommandParam(
        name=name,
        type=cast(ParamType, raw_type),
        required=required,
        enum=enum,
        flag=flag,
        positional=positional,
    )


def _optional_enum(value: object, name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _strings(value, f"{name} enum")


def _optional_strings(value: object, label: str) -> tuple[str, ...]:
    if value is None or value == []:
        return ()
    return _strings(value, label)


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CommandOpError(f"{label} must be a non-empty list of strings")
    items: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item:
            raise CommandOpError(f"{label} must be a non-empty list of strings")
        items.append(item)
    return tuple(items)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommandOpError(f"{label} must be a non-empty string")
    return value


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise CommandOpError(f"{label} must be an object")
    parsed: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise CommandOpError(f"{label} keys must be strings")
        parsed[key] = item
    return parsed


def _taptap_module_present() -> bool:
    return importlib.util.find_spec("taptap") is not None


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr

