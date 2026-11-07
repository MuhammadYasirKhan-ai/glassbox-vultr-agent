"""Hostile-code demo: proves the sandbox holds even with the policy gate bypassed."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from app.sandbox import Sandbox, SandboxResult, parse_harness

Check = Callable[[SandboxResult, dict[str, object] | None], bool]


@dataclass(frozen=True)
class Case:
    name: str
    code: str
    check: Check
    timeout_s: int = 8


def _blocked(_: SandboxResult, h: dict[str, object] | None) -> bool:
    return h is not None and "BLOCKED" in str(h.get("stdout", ""))


CASES: list[Case] = [
    Case("Network egress to 1.1.1.1", """
import socket
try:
    socket.create_connection(("1.1.1.1", 53), timeout=3); print("ESCAPED")
except OSError as e:
    print("BLOCKED", e)
""", _blocked),
    Case("Write outside the sandbox (/etc)", """
try:
    open("/etc/pwned", "w").write("x"); print("ESCAPED")
except OSError as e:
    print("BLOCKED", e)
""", _blocked),
    Case("Read API keys from environment", """
import os
prefixes = ("VULTR", "GEMINI", "GOOGLE", "DS_LLM", "AWS", "ANTHROPIC", "OPENAI", "LLM_")
leaked = [k for k in os.environ if k.upper().startswith(prefixes)]
print("ESCAPED " + str(leaked) if leaked else "BLOCKED no secrets in env")
""", _blocked),
    Case("Fork bomb", """
import os, time
kids = 0
try:
    for _ in range(2000):
        if os.fork() == 0:
            time.sleep(3); os._exit(0)
        kids += 1
    print("ESCAPED", kids)
except OSError as e:
    print("BLOCKED after", kids, "forks:", e)
""", _blocked),
    Case("Memory bomb (4 GB)", "x = bytearray(4 * 1024**3)\nprint('ESCAPED')\n",
         lambda r, h: r.exit_code in (137, -9) or (h is not None and not h.get("ok"))),
    Case("Infinite loop", "while True:\n    pass\n", lambda r, h: r.timed_out, timeout_s=4),
]


async def iter_demo(sandbox: Sandbox) -> AsyncIterator[dict[str, object]]:
    """Yield one result per hostile case, as soon as it finishes."""
    for i, c in enumerate(CASES):
        res = await sandbox.run(c.code, b"a,b\n1,2\n", timeout_s=c.timeout_s)
        h = parse_harness(res.stdout)
        yield {
            "index": i,
            "attack": c.name,
            "contained": bool(c.check(res, h)) and res.destroyed,
            "exit_code": res.exit_code,
            "timed_out": res.timed_out,
            "container_destroyed": res.destroyed,
            "evidence": (str(h.get("stdout", "")).strip()[:160] if h else res.stderr[:160]),
        }


async def run_demo(sandbox: Sandbox) -> list[dict[str, object]]:
    return [row async for row in iter_demo(sandbox)]
