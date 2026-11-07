"""Layer 1 of containment: a static policy gate on model-written code.

Defense in depth only. The real boundary is the sandbox (layer 2).
"""
from __future__ import annotations

import ast

DENIED_MODULES = frozenset(
    {"socket", "subprocess", "ctypes", "requests", "urllib", "http", "ftplib", "smtplib",
     "shutil", "multiprocessing", "pty", "telnetlib", "asyncio", "importlib", "pip"}
)
DENIED_CALLS = frozenset({"eval", "exec", "compile", "__import__"})


def check_code(code: str) -> list[str]:
    """Return a list of policy violations (empty means allowed)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in DENIED_MODULES:
                    violations.append(f"import of '{a.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in DENIED_MODULES:
                violations.append(f"import from '{node.module}' is not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in DENIED_CALLS:
                violations.append(f"call to '{node.func.id}()' is not allowed")
    return violations
