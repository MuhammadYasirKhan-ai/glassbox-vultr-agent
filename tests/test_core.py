from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.containment import run_demo
from app.llm import Message
from app.main import Services, create_app
from app.orchestrator import Orchestrator, csv_preview, extract_code
from app.policy import check_code
from app.sandbox import SandboxResult, build_docker_args, parse_harness
from app.verify import unverified_numbers

CSV = b"a,b,c\n" + b"".join(f"{i},{i*2},x\n".encode() for i in range(30))

GOOD = {"task": "classification", "target": "churned", "metric_name": "accuracy",
        "model_score": 0.8123, "baseline_score": 0.55, "n_rows": 800,
        "importances": [{"feature": "contract", "mean": 0.1234, "std": 0.01}]}


def harness_out(ok: bool = True, result: dict[str, object] | None = None, error: str = "") -> str:
    return json.dumps({"ok": ok, "stdout": json.dumps(result or GOOD) + "\n", "error": error,
                       "figures": ["aGk="]})


class FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self.replies, self.calls = replies, 0

    async def complete(self, messages: list[Message]) -> str:
        r = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return r


class FakeSandbox:
    def __init__(self, outs: list[tuple[int, str]]) -> None:
        self.outs, self.n = outs, 0

    async def run(self, code: str, data: bytes, timeout_s: int | None = None) -> SandboxResult:
        code_, out = self.outs[min(self.n, len(self.outs) - 1)]
        self.n += 1
        return SandboxResult(code_, out, "", False, 0.1, True, ["--network", "none"], "c", "d")


def test_policy() -> None:
    assert check_code("import pandas as pd\nprint(1)") == []
    assert check_code("import socket")
    assert check_code("from subprocess import run")
    assert check_code("eval('1')")
    assert check_code("def (")


def test_extract_and_preview() -> None:
    assert extract_code("x\n```python\nprint(1)\n```\ny") == "print(1)"
    assert extract_code("print(2)") == "print(2)"
    assert csv_preview(b"a,b\n1,2\n3,4\n").splitlines()[0] == "a,b"


def test_verify_numbers() -> None:
    assert unverified_numbers("Accuracy was 0.8123 vs baseline 55%.", GOOD) == []
    assert unverified_numbers("Accuracy was 0.99.", GOOD) == ["0.99"]


def test_docker_flags() -> None:
    a = build_docker_args("n", "/t", Settings(sandbox_runtime="runsc"))
    s = " ".join(a)
    for needle in ("--network none", "--read-only", "--cap-drop ALL", "--pids-limit", "--runtime runsc"):
        assert needle in s


def test_parse_harness() -> None:
    assert parse_harness("noise\n" + harness_out())["ok"] is True  # type: ignore[index]
    assert parse_harness("garbage") is None


@pytest.mark.asyncio
async def test_self_heal_then_success() -> None:
    llm = FakeLLM(["```python\nraise Exception()\n```", "```python\nprint(1)\n```",
                   "Accuracy 0.8123 beats baseline 0.55."])
    sb = FakeSandbox([(1, harness_out(False, error="Traceback boom")), (0, harness_out())])
    out = await Orchestrator(llm, sb, Settings()).analyze("g", None, b"a,b\n1,2\n")
    assert out.ok and len(out.attempts) == 2
    assert out.attempts[0].outcome == "failed" and out.attempts[1].outcome == "success"
    assert out.unverified == [] and out.receipt and out.receipt["container_destroyed"] is True


@pytest.mark.asyncio
async def test_policy_block_then_fail() -> None:
    llm = FakeLLM(["```python\nimport socket\n```"])
    out = await Orchestrator(llm, FakeSandbox([(0, "")]), Settings()).analyze("g", None, b"a\n1\n")
    assert not out.ok and all(a.outcome == "blocked by policy" for a in out.attempts)


@pytest.mark.asyncio
async def test_containment_demo_with_fake() -> None:
    class S:
        async def run(self, code: str, data: bytes, timeout_s: int | None = None) -> SandboxResult:
            blocked = json.dumps({"ok": True, "stdout": "BLOCKED x", "error": "", "figures": []})
            if "bytearray" in code:
                return SandboxResult(137, "", "", False, 1, True)
            if "while True" in code:
                return SandboxResult(-9, "", "", True, 4, True)
            return SandboxResult(0, blocked, "", False, 1, True)

    rows = await run_demo(S())
    assert len(rows) == 6 and all(r["contained"] for r in rows)


def make_client() -> TestClient:
    s = Settings(max_upload_bytes=1000, rate_limit_per_min=3)  # CSV fixture is < 1000 bytes
    svc = Services(s, FakeLLM(["```python\nprint(1)\n```", "Accuracy 0.8123."]),
                   FakeSandbox([(0, harness_out())]))
    return TestClient(create_app(svc))


def test_api() -> None:
    c = make_client()
    assert c.get("/healthz").json()["status"] == "ok"
    r = c.post("/api/analyze", files={"file": ("d.csv", CSV)}, data={"goal": "x"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.post("/api/analyze", files={"file": ("d.csv", b"x" * 2000)}).status_code == 413
    assert c.post("/api/analyze", files={"file": ("d.csv", b"  ")}).status_code in (422, 429)


def test_rate_limit() -> None:
    c = make_client()
    codes = [c.post("/api/analyze", files={"file": ("d.csv", CSV)}).status_code for _ in range(5)]
    assert 429 in codes


def test_provider_resolution_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm import GeminiClient, OpenAICompatClient, build_client, resolve_provider

    monkeypatch.setenv("DS_LLM_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    s = Settings.from_env()
    assert s.gemini_model == "gemini-3.5-flash-lite" and resolve_provider(s) == "gemini"
    assert isinstance(build_client(s), GeminiClient)
    v = Settings(provider="auto", vultr_api_key="x", vultr_model="m")
    assert resolve_provider(v) == "vultr" and isinstance(build_client(v), OpenAICompatClient)


@pytest.mark.asyncio
async def test_gemini_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app import llm

    calls = {"n": 0}

    async def fake_sleep(_: float) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    out = await llm.GeminiClient("k", "m").complete([{"role": "user", "content": "x"}])
    assert out == "hi" and calls["n"] == 3


def test_validate_csv() -> None:
    from app.orchestrator import validate_csv

    assert validate_csv(CSV, None) is None
    assert validate_csv(CSV, "c") is None
    assert "not found" in (validate_csv(CSV, "zzz") or "")
    assert validate_csv(b"a\n1\n", None)
    assert validate_csv(b"\x00\x01", None)
    assert validate_csv(b"a,b\n1,2\n", None)  # too few rows
    assert validate_csv(b"\xff\xfe\x00", None)


def test_public_flags_hide_names_and_paths() -> None:
    a = build_docker_args("glassbox-abc", "/tmp/secret-dir", Settings())
    flags = " ".join(__import__("app.sandbox", fromlist=["x"]).public_flags(a))
    assert "glassbox-abc" not in flags and "secret-dir" not in flags and "--network none" in flags
    assert "--name" not in flags and "--rm" not in flags


def test_client_ip_and_ratelimit_cost() -> None:
    svc = Services(Settings(rate_limit_per_min=5), FakeLLM(["x"]), FakeSandbox([(0, "")]))
    assert svc.allow("1.1.1.1", cost=4) and not svc.allow("1.1.1.1", cost=4)
    assert svc.allow("2.2.2.2")


def test_stream_endpoints() -> None:
    class Demo:
        async def run(self, code: str, data: bytes, timeout_s: int | None = None) -> SandboxResult:
            if "bytearray" in code:
                return SandboxResult(137, "", "", False, 1, True)
            if "while True" in code:
                return SandboxResult(-9, "", "", True, 4, True)
            ok = json.dumps({"ok": True, "stdout": "BLOCKED x", "error": "", "figures": []})
            return SandboxResult(0, ok, "", False, 1, True)

    svc = Services(Settings(rate_limit_per_min=60), FakeLLM(["```python\nprint(1)\n```", "Accuracy 0.8123."]),
                   FakeSandbox([(0, harness_out())]))
    c = TestClient(create_app(svc))
    lines = [json.loads(x) for x in c.post("/api/analyze/stream", files={"file": ("d.csv", CSV)}).text.splitlines()]
    kinds = [x["type"] for x in lines]
    assert kinds[0] == "stage" and "attempt" in kinds and kinds[-1] == "final" and lines[-1]["ok"] is True
    svc2 = Services(Settings(rate_limit_per_min=60), FakeLLM(["x"]), Demo())
    lines = [json.loads(x) for x in TestClient(create_app(svc2)).get("/api/containment-demo/stream").text.splitlines()]
    assert lines[-1] == {"type": "final", "all_contained": True}
    assert sum(1 for x in lines if x["type"] == "case_result") == 6
    r = c.get("/healthz")
    assert r.headers["content-security-policy"].startswith("default-src 'self'")


def test_llm_error_in_stream_becomes_error_event() -> None:
    from app.llm import LLMError

    class Boom:
        async def complete(self, messages: list[Message]) -> str:
            raise LLMError("quota")

    svc = Services(Settings(rate_limit_per_min=60), Boom(), FakeSandbox([(0, "")]))
    lines = [json.loads(x) for x in TestClient(create_app(svc)).post(
        "/api/analyze/stream", files={"file": ("d.csv", CSV)}).text.splitlines()]
    assert lines[-1]["type"] == "error" and "quota" in lines[-1]["message"]


@pytest.mark.asyncio
async def test_gemini_ignores_thought_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app import llm

    body = {"candidates": [{"content": {"parts": [{"text": "secret thoughts", "thought": True},
                                                  {"text": "answer"}]}}]}
    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)), **kw))
    assert await llm.GeminiClient("k", "m").complete([{"role": "user", "content": "x"}]) == "answer"


@pytest.mark.asyncio
async def test_network_errors_become_llmerror(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app import llm

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(boom), **kw))
    msgs: list[Message] = [{"role": "user", "content": "x"}]
    with pytest.raises(llm.LLMError):
        await llm.GeminiClient("k", "m").complete(msgs)
    with pytest.raises(llm.LLMError):
        await llm.OpenAICompatClient("http://x", "k", "m").complete(msgs)
