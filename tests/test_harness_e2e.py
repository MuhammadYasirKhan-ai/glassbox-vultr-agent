"""Runs the REAL in-container harness + a known-good ML script locally (no Docker needed)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("sklearn")
pytest.importorskip("matplotlib")
ROOT = Path(__file__).resolve().parent.parent


def test_harness_with_reference_script(tmp_path: Path) -> None:
    work, out = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    (work / "script.py").write_text((ROOT / "tests/fixtures/reference_script.py").read_text())
    (work / "data.csv").write_text((ROOT / "samples/churn.csv").read_text())
    env = {"GLASSBOX_WORK": str(work), "GLASSBOX_OUT": str(out), "MPLCONFIGDIR": str(tmp_path / "mpl"),
           "PATH": "/usr/bin:/bin"}
    p = subprocess.run([sys.executable, str(ROOT / "sandbox/harness.py")], env=env, capture_output=True,
                       text=True, timeout=120, check=False)
    from app.orchestrator import last_json
    from app.sandbox import parse_harness

    h = parse_harness(p.stdout)
    assert p.returncode == 0 and h and h["ok"] is True, p.stderr + p.stdout[-500:]
    res = last_json(str(h["stdout"]))
    assert res and res["task"] == "classification" and res["model_score"] > res["baseline_score"]  # type: ignore[operator]
    assert len(h["figures"]) == 1  # type: ignore[arg-type]
    names = [i["feature"] for i in res["importances"]]  # type: ignore[union-attr]
    assert "contract" in names or "support_calls" in names
    json.dumps(res)


def test_harness_reports_script_errors(tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "script.py").write_text("raise ValueError('boom')")
    env = {"GLASSBOX_WORK": str(work), "GLASSBOX_OUT": str(tmp_path / "o"), "MPLCONFIGDIR": str(tmp_path),
           "PATH": "/usr/bin:/bin"}
    p = subprocess.run([sys.executable, str(ROOT / "sandbox/harness.py")], env=env, capture_output=True,
                       text=True, timeout=60, check=False)
    from app.sandbox import parse_harness

    h = parse_harness(p.stdout)
    assert p.returncode == 1 and h and h["ok"] is False and "boom" in str(h["error"])
