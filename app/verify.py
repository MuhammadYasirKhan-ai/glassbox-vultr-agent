"""Claim check: every number in the explanation must trace back to executed output."""
from __future__ import annotations

import re

_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?%?")


def _collect(obj: object, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)


def _matches(value: float, known: float, pct: bool) -> bool:
    if pct:
        return abs(value - known * 100) <= max(0.6, 0.006 * abs(known * 100))
    return abs(value - known) <= max(0.006 * abs(known), 0.006)


def unverified_numbers(explanation: str, result: dict[str, object]) -> list[str]:
    """Return numbers in the text that do not match any number in the executed result."""
    known: list[float] = []
    _collect(result, known)
    bad: list[str] = []
    for tok in _NUM.findall(explanation):
        pct = tok.endswith("%")
        val = float(tok.rstrip("%"))
        if not pct and val in (1.0, 2.0, 3.0):  # ordinals / "top 3"
            continue
        if not any(_matches(val, k, pct) or _matches(val, k, False) for k in known):
            bad.append(tok)
    return bad
