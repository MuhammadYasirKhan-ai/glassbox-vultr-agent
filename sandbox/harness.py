"""Runs inside the sandbox container. Executes /work/script.py and prints ONE JSON line."""
import base64
import contextlib
import glob
import io
import json
import os
import sys
import traceback

WORK = os.environ.get("GLASSBOX_WORK", "/work")
OUT = os.environ.get("GLASSBOX_OUT", "/tmp/out")
os.makedirs(OUT, exist_ok=True)
os.chdir(OUT)

buf = io.StringIO()
ok, err = True, ""
try:
    import matplotlib

    matplotlib.use("Agg")
    with open(os.path.join(WORK, "script.py"), encoding="utf-8") as fh:
        src = fh.read()
    with contextlib.redirect_stdout(buf):
        exec(compile(src, "script.py", "exec"), {"__name__": "__main__"})
except BaseException:  # noqa: BLE001 - everything the untrusted script does is reported
    ok, err = False, traceback.format_exc()[-3000:]

figures = []
for path in sorted(glob.glob(os.path.join(OUT, "*.png")))[:4]:
    if os.path.getsize(path) <= 1_000_000:
        with open(path, "rb") as fh:
            figures.append(base64.b64encode(fh.read()).decode())

sys.stdout.write("\n" + json.dumps({"ok": ok, "stdout": buf.getvalue()[-20000:], "error": err,
                                    "figures": figures}) + "\n")
sys.stdout.flush()
sys.exit(0 if ok else 1)
