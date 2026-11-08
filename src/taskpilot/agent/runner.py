"""Restricted subprocess runner: no shell, structured args, bounded time and output."""
from __future__ import annotations

import asyncio
import os
import shutil
import time

# Minimal environment: do not leak the agent's secrets (tokens etc.) to child processes.
_SAFE_ENV_KEYS = ("SystemRoot", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "COMSPEC")


class ManagedProcesses:
    """Tracks processes TaskPilot started, so cancel/e-stop only touches its own."""

    def __init__(self) -> None:
        self.by_task: dict[str, set[asyncio.subprocess.Process]] = {}

    def add(self, task_id: str, p: asyncio.subprocess.Process) -> None:
        self.by_task.setdefault(task_id, set()).add(p)

    def discard(self, task_id: str, p: asyncio.subprocess.Process) -> None:
        self.by_task.get(task_id, set()).discard(p)

    def kill_task(self, task_id: str) -> int:
        n = 0
        for p in list(self.by_task.get(task_id, ())):
            if p.returncode is None:
                _kill_tree(p.pid)
                n += 1
        return n

    def kill_all(self) -> int:
        return sum(self.kill_task(t) for t in list(self.by_task))


def _kill_tree(pid: int) -> None:
    try:
        import psutil
        proc = psutil.Process(pid)
        for c in proc.children(recursive=True):
            c.kill()
        proc.kill()
    except Exception:
        pass


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    buf, truncated = bytearray(), False
    while chunk := await stream.read(4096):
        if len(buf) < cap:
            buf.extend(chunk[: cap - len(buf)])
        if len(buf) >= cap:
            truncated = True   # keep draining so the child does not block on a full pipe
    return bytes(buf), truncated


async def run_command(managed: ManagedProcesses, task_id: str, executable: str, args: list[str],
                      cwd: str, timeout_s: float, max_output_bytes: int) -> dict:
    exe = shutil.which(executable)
    if exe is None:
        return {"error": f"executable '{executable}' not found", "exit_code": None}
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        exe, *args, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    managed.add(task_id, proc)
    timed_out = False
    try:
        out_t = asyncio.create_task(_read_capped(proc.stdout, max_output_bytes))
        err_t = asyncio.create_task(_read_capped(proc.stderr, max_output_bytes))
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_tree(proc.pid)
            await proc.wait()
        (out, o_trunc), (err, e_trunc) = await asyncio.gather(out_t, err_t)
    finally:
        managed.discard(task_id, proc)
    return {
        "exit_code": proc.returncode, "timed_out": timed_out,
        "stdout": out.decode("utf-8", "replace"), "stderr": err.decode("utf-8", "replace"),
        "truncated": o_trunc or e_trunc, "duration_ms": (time.monotonic() - t0) * 1000,
    }
