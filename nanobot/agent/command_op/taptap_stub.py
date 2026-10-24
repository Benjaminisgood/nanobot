#!/usr/bin/env python3
"""Linux stub for ``taptap --describe-json``.

This process prints a ``taptap.describe/v1`` catalog and exits. It does not
execute TAP commands and it is not the lab Windows TAP binary. Flag and
positional names match ``tests/agent/fixtures/taptap_describe_sample.json``.

Point nanobot at it with ``NANOBOT_TAPTAP=stub`` or with the path of this file.
An install also exposes the console script ``taptap-describe-stub``.
"""

from __future__ import annotations

import json
import sys

# Catalog executable stays ``taptap``. Approved CommandOps still invoke that
# prefix; this stub only answers the describe probe.
STUB_CATALOG: dict[str, object] = {
    "schema": "taptap.describe/v1",
    "note": (
        "Linux describe-json stub for NANOBOT_TAPTAP. Not the lab Windows TAP "
        "binary. Flag and positional names match the in-repo taptap.describe/v1 "
        "sample and are not a hardware export."
    ),
    "executable": ["taptap"],
    "commands": [
        {
            "app": "project_config",
            "command": "init_tap",
            "side_effects": "writes_new",
            "params": [
                {"name": "output", "type": "path", "required": True, "flag": "--output"},
            ],
        },
        {
            "app": "wyx_inspect",
            "command": "wyx_summary",
            "side_effects": "read_only",
            "params": [
                {"name": "measurement_dir", "type": "path", "required": True, "positional": 0},
            ],
        },
        {
            "app": "wyx_tables",
            "command": "experiment_summary",
            "side_effects": "read_only",
            "params": [
                {"name": "measurement_dir", "type": "path", "required": True, "positional": 0},
            ],
        },
        {
            "app": "process_pulse_sizes",
            "command": "pulse_size",
            "side_effects": "writes_new",
            "red_line_ids": ["no_overwrite_wyx", "pressure_unit_match_header"],
            "params": [
                {"name": "measurement_dir", "type": "path", "required": True, "positional": 0},
                {"name": "config", "type": "path", "required": True, "flag": "--config"},
                {"name": "output_dir", "type": "path", "required": True, "flag": "--output"},
                {
                    "name": "pressure_unit",
                    "type": "string",
                    "required": True,
                    "flag": "--pressure-unit",
                    "enum": ["bar", "mbar"],
                },
            ],
        },
    ],
}

_DESCRIBE_FLAG = "--describe-json"


def main(argv: list[str] | None = None) -> None:
    """Print the stub catalog, or refuse any invocation other than describe."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args != [_DESCRIBE_FLAG]:
        print(
            "taptap-describe-stub only implements --describe-json. "
            "It does not run TAP commands or the lab Windows TAP binary.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    json.dump(STUB_CATALOG, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
