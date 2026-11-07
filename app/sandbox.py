"""Layer 2 of containment: one throwaway, locked-down container per run."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.config import Settings

MAX_OUTPUT_BYTES = 3_000_000


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    destroyed: bool
    flags: list[str] = field(default_factory=list)
    code_sha256: str = ""
    data_sha256: str = ""


class Sandbox(Protocol):
    async def run(self, code: str, data: bytes, timeout_s: int | None = None) -> SandboxResult: ...


def public_flags(args: list[str]) -> list[str]:
    """Flags worth showing in a receipt: no container name, no host paths. Flag+value are paired."""
    tokens = ["type=bind,dst=/work,readonly" if t.startswith("type=bind") else t for t in args[5:-1]]
    out: list[str] = []
    for tok in tokens:
        if tok.startswith("-") or not out:
            out.append(tok)
        else:
            out[-1] = f"{out[-1]} {tok}"
    return out


def build_docker_args(name: str, workdir: str, s: Settings) -> list[str]:
    """The exact isolation flags. Returned verbatim in every receipt."""
    args = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", s.sandbox_memory, "--memory-swap", s.sandbox_memory,
        "--cpus", "1",
        "--ulimit", "nofile=128", "--ulimit", "fsize=20000000",
        "--user", "65534:65534",
        "--tmpfs", "/tmp:rw,nosuid,size=96m",
        "-e", "MPLCONFIGDIR=/tmp/mpl",
        "--mount", f"type=bind,src={workdir},dst=/work,readonly",
    ]
    if s.sandbox_runtime:
        args += ["--runtime", s.sandbox_runtime]
    args.append(s.sandbox_image)
    return args


class DockerSandbox:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def run(self, code: str, data: bytes, timeout_s: int | None = None) -> SandboxResult:
        timeout = timeout_s or self._s.sandbox_timeout_s
        name = f"glassbox-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory(prefix="gb-") as tmp:
            os.chmod(tmp, 0o755)
            script, csv_path = Path(tmp, "script.py"), Path(tmp, "data.csv")
            script.write_text(code, encoding="utf-8")
            csv_path.write_bytes(data)
            os.chmod(script, 0o444)
            os.chmod(csv_path, 0o444)
            args = build_docker_args(name, tmp, self._s)
            start = time.monotonic()
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            timed_out = False
            try:
                out, err = await asyncio.wait_for(self._bounded(proc), timeout=timeout)
            except TimeoutError:
                timed_out = True
                out, err = b"", b"killed: wall-clock limit exceeded"
            finally:
                await self._kill(name)
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
            destroyed = await self._is_gone(name)
            return SandboxResult(
                exit_code=-9 if timed_out else (proc.returncode or 0),
                stdout=out.decode("utf-8", "replace"),
                stderr=err.decode("utf-8", "replace")[-4000:],
                timed_out=timed_out,
                duration_s=round(time.monotonic() - start, 2),
                destroyed=destroyed,
                flags=public_flags(args),
                code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                data_sha256=hashlib.sha256(data).hexdigest(),
            )

    @staticmethod
    async def _bounded(proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        async def read(stream: asyncio.StreamReader | None) -> bytes:
            buf = bytearray()
            while stream is not None:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                if len(buf) < MAX_OUTPUT_BYTES:
                    buf += chunk[: MAX_OUTPUT_BYTES - len(buf)]
            return bytes(buf)

        out, err = await asyncio.gather(read(proc.stdout), read(proc.stderr))
        await proc.wait()
        return out, err

    @staticmethod
    async def _kill(name: str) -> None:
        p = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await p.wait()

    @staticmethod
    async def _is_gone(name: str) -> bool:
        p = await asyncio.create_subprocess_exec(
            "docker", "inspect", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        return (await p.wait()) != 0


def parse_harness(stdout: str) -> dict[str, object] | None:
    """The in-container harness prints exactly one JSON line."""
    for line in reversed(stdout.strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
