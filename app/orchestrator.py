"""plan -> policy gate -> sandbox -> self-heal -> explain -> verify -> receipt."""
from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from app.config import Settings
from app.llm import LLMClient, Message
from app.policy import check_code
from app.prompts import CODE_SYSTEM, EXPLAIN_SYSTEM
from app.sandbox import Sandbox, SandboxResult, parse_harness
from app.verify import unverified_numbers

Emit = Callable[[dict[str, object]], Awaitable[None]]
MIN_ROWS = 20
_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    m = _FENCE.search(text)
    return (m.group(1) if m else text).strip()


def csv_preview(data: bytes, rows: int = 5) -> str:
    reader = csv.reader(io.StringIO(data.decode("utf-8", "replace")))
    out: list[list[str]] = []
    for i, row in enumerate(reader):
        out.append([c[:40] for c in row[:60]])
        if i >= rows:
            break
    return "\n".join(",".join(r) for r in out)


def validate_csv(data: bytes, target: str | None) -> str | None:
    """Cheap pre-flight checks so we never waste LLM calls on a bad upload."""
    if b"\x00" in data:
        return "This looks like a binary file, not a CSV."
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "The file must be UTF-8 encoded CSV."
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        return f"Could not parse CSV: {exc}"
    if not rows or len(rows[0]) < 2:
        return "The CSV needs a header row with at least 2 columns."
    if len(rows) - 1 < MIN_ROWS:
        return f"The CSV needs at least {MIN_ROWS} data rows (found {len(rows) - 1})."
    if target and target not in rows[0]:
        cols = ", ".join(rows[0][:15])
        return f"Target column '{target}' not found. Columns: {cols}"
    return None


def last_json(stdout: str) -> dict[str, object] | None:
    for line in reversed(stdout.strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


@dataclass
class Attempt:
    n: int
    code: str
    policy_violations: list[str]
    exit_code: int | None
    stderr_tail: str
    duration_s: float | None
    outcome: str


@dataclass
class AnalysisOutcome:
    ok: bool
    attempts: list[Attempt] = field(default_factory=list)
    result: dict[str, object] | None = None
    figures: list[str] = field(default_factory=list)
    explanation: str = ""
    unverified: list[str] = field(default_factory=list)
    receipt: dict[str, object] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Orchestrator:
    def __init__(self, llm: LLMClient, sandbox: Sandbox, settings: Settings) -> None:
        self._llm, self._sb, self._s = llm, sandbox, settings

    async def analyze(
        self, goal: str, target: str | None, data: bytes, emit: Emit | None = None
    ) -> AnalysisOutcome:
        async def ev(event: dict[str, object]) -> None:
            if emit is not None:
                await emit(event)

        outcome = AnalysisOutcome(ok=False)
        user = (
            f"Goal: {goal}\nTarget column: {target or '(use last column)'}\n"
            f"CSV preview (first rows):\n{csv_preview(data)}"
        )
        messages: list[Message] = [
            {"role": "system", "content": CODE_SYSTEM},
            {"role": "user", "content": user},
        ]
        final: SandboxResult | None = None
        harness: dict[str, object] | None = None
        for n in range(1, self._s.max_attempts + 1):
            await ev({"type": "stage", "stage": "plan", "attempt": n})
            reply = await self._llm.complete(messages)
            code = extract_code(reply)
            await ev({"type": "stage", "stage": "policy", "attempt": n})
            violations = check_code(code)
            if violations:
                att = Attempt(n, code, violations, None, "", None, "blocked by policy")
                outcome.attempts.append(att)
                await ev({"type": "attempt", **asdict(att)})
                messages += [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": "Policy violations: " + "; ".join(violations)
                     + ". Fix and resend the full script."},
                ]
                continue
            await ev({"type": "stage", "stage": "sandbox", "attempt": n})
            res = await self._sb.run(code, data)
            h = parse_harness(res.stdout)
            err = str(h.get("error", "")) if h else res.stderr
            success = res.exit_code == 0 and h is not None and bool(h.get("ok"))
            tail = (err or res.stderr)[-1500:]
            att = Attempt(n, code, [], res.exit_code, tail, res.duration_s,
                          "success" if success else "failed")
            outcome.attempts.append(att)
            await ev({"type": "attempt", **asdict(att)})
            if success:
                final, harness = res, h
                break
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"The script failed:\n{tail}\nFix it and resend the full script."},
            ]
        if final is None or harness is None:
            outcome.error = "No attempt produced a verified result."
            return outcome

        result = last_json(str(harness.get("stdout", "")))
        if result is None:
            outcome.error = "Script ran but did not print the required JSON result."
            return outcome
        figs = harness.get("figures", [])
        outcome.figures = [f for f in figs if isinstance(f, str)] if isinstance(figs, list) else []
        outcome.result = result
        await ev({"type": "stage", "stage": "explain"})
        expl = await self._llm.complete([
            {"role": "system", "content": EXPLAIN_SYSTEM},
            {"role": "user", "content": "RESULT JSON:\n" + json.dumps(result)},
        ])
        outcome.explanation = expl.strip()
        await ev({"type": "stage", "stage": "verify"})
        outcome.unverified = unverified_numbers(outcome.explanation, result)
        outcome.receipt = {
            "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "code_sha256": final.code_sha256,
            "data_sha256": final.data_sha256,
            "isolation_flags": final.flags,
            "container_destroyed": final.destroyed,
            "exit_code": final.exit_code,
            "duration_s": final.duration_s,
            "attempts": len(outcome.attempts),
            "secrets_in_sandbox": False,
        }
        outcome.ok = True
        return outcome
