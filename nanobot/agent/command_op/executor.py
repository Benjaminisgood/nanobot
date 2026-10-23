"""Run an approved CommandOp argv with the catalog executable.

The executor receives the frozen argv and does not rebuild flags. It records
the exit code, a workspace log, and sha256 hashes of workspace files named by
that argv. Red-line ids are not read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from nanobot.agent.command_op.models import Artifact, CommandExecution, CommandOp
from nanobot.agent.tools.path_utils import resolve_workspace_path
from nanobot.security.workspace_policy import WorkspaceBoundaryError

_LOG_LIMIT = 1_000_000


class TapTapExecutor:
    """Subprocess executor registered through ``TapAdapter.register_executor``."""

    def __init__(self, workspace: Path, *, timeout_s: float = 120) -> None:
        self.workspace = workspace
        self.timeout_s = timeout_s

    async def execute(self, op: CommandOp, argv: tuple[str, ...]) -> CommandExecution:
        log_path = self._log_path(op.op_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        header = "argv: " + json.dumps(list(argv), ensure_ascii=False) + "\n"
        if not argv or any(not token for token in argv):
            _write_log(log_path, header + "empty argv\n")
            return CommandExecution(exit_code=127, log_uri=self._log_uri(log_path))
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self.workspace),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            _write_log(log_path, header + f"{exc}\n")
            return CommandExecution(exit_code=127, log_uri=self._log_uri(log_path))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            _write_log(log_path, header + "timed out\n")
            return CommandExecution(
                exit_code=124,
                log_uri=self._log_uri(log_path),
                artifacts=self._artifacts(argv, log_path),
            )
        exit_code = process.returncode if process.returncode is not None else 1
        _write_log(log_path, header + _stream("stdout", stdout) + _stream("stderr", stderr))
        return CommandExecution(
            exit_code=exit_code,
            log_uri=self._log_uri(log_path),
            artifacts=self._artifacts(argv, log_path),
        )

    def _log_path(self, op_id: str) -> Path:
        safe = op_id if op_id and op_id not in {".", ".."} and "/" not in op_id and "\\" not in op_id else "unknown"
        return self.workspace / "command_ops" / "logs" / f"{safe}.log"

    def _log_uri(self, path: Path) -> str:
        return path.resolve().relative_to(self.workspace.resolve()).as_posix()

    def _artifacts(self, argv: tuple[str, ...], log_path: Path) -> tuple[Artifact, ...]:
        found: list[Artifact] = []
        seen: set[Path] = set()
        log_resolved = log_path.resolve()
        for token in argv:
            path = self._contained(token)
            if path is None:
                continue
            if path.is_file():
                self._add_file(found, seen, path, log_resolved)
                continue
            if path.is_dir():
                for child in sorted(path.iterdir()):
                    if child.is_symlink() or not child.is_file():
                        continue
                    contained = self._contained(str(child))
                    if contained is not None:
                        self._add_file(found, seen, contained, log_resolved)
        return tuple(found)

    def _contained(self, token: str) -> Path | None:
        try:
            return resolve_workspace_path(
                token,
                workspace=self.workspace,
                allowed_dir=self.workspace,
                include_media_dir=False,
            )
        except (OSError, ValueError, WorkspaceBoundaryError):
            return None

    def _add_file(
        self,
        found: list[Artifact],
        seen: set[Path],
        path: Path,
        log_resolved: Path,
    ) -> None:
        resolved = path.resolve()
        if resolved == log_resolved or resolved in seen or not resolved.is_file():
            return
        try:
            relative = resolved.relative_to(self.workspace.resolve())
        except ValueError:
            return
        seen.add(resolved)
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        found.append(Artifact(path=relative.as_posix(), sha=digest))


def _write_log(path: Path, text: str) -> None:
    if len(text) > _LOG_LIMIT:
        text = text[:_LOG_LIMIT] + "\ntruncated\n"
    path.write_text(text, encoding="utf-8")


def _stream(label: str, payload: bytes | None) -> str:
    text = (payload or b"").decode("utf-8", errors="replace")
    return f"--- {label} ---\n{text}"
