"""FastAPI entrypoint: JSON endpoints, NDJSON streaming endpoints, static UI."""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.containment import CASES, iter_demo, run_demo
from app.llm import LLMClient, LLMError, build_client, resolve_provider
from app.orchestrator import Emit, Orchestrator, validate_csv
from app.sandbox import DockerSandbox, Sandbox

log = logging.getLogger("glassbox")
STATIC = Path(__file__).resolve().parent.parent / "static"
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
DEMO_COST = 4  # the containment demo launches 6 containers, so it costs more rate-limit budget


def client_ip(request: Request) -> str:
    """Real client IP. Only trust X-Forwarded-For when the peer is our local reverse proxy."""
    host = request.client.host if request.client else "?"
    if host in ("127.0.0.1", "::1"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return host


class Services:
    def __init__(self, settings: Settings, llm: LLMClient, sandbox: Sandbox) -> None:
        self.settings = settings
        self.llm = llm
        self.sandbox = sandbox
        self.sem = asyncio.Semaphore(settings.max_concurrent)
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.tasks: set[asyncio.Task[None]] = set()

    def allow(self, ip: str, cost: int = 1) -> bool:
        now = time.monotonic()
        if len(self.hits) > 2000:
            self.hits = defaultdict(
                deque, {k: v for k, v in self.hits.items() if v and now - v[-1] < 60}
            )
        q = self.hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) + cost > self.settings.rate_limit_per_min:
            return False
        q.extend([now] * cost)
        return True


def create_app(services: Services | None = None) -> FastAPI:
    app = FastAPI(title="GlassBox", version="1.0.0", docs_url=None, redoc_url=None)
    if services is None:
        cfg = Settings.from_env()
        services = Services(cfg, build_client(cfg), DockerSandbox(cfg))
    svc = services

    def get_svc() -> Services:
        return svc

    Dep = Annotated[Services, Depends(get_svc)]

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    def stream(work: Callable[[Emit], Awaitable[None]]) -> StreamingResponse:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

        async def emit(event: dict[str, object]) -> None:
            await queue.put(event)

        async def runner() -> None:
            try:
                await emit({"type": "stage", "stage": "queued"})
                async with svc.sem:
                    await work(emit)
            except LLMError as exc:
                await emit({"type": "error", "message": f"Model provider error: {exc}"})
            except Exception:
                log.exception("stream job failed")
                await emit({"type": "error", "message": "Internal error. Check server logs."})
            finally:
                await queue.put(None)

        task = asyncio.create_task(runner())
        svc.tasks.add(task)
        task.add_done_callback(svc.tasks.discard)

        async def gen() -> AsyncIterator[str]:
            while (event := await queue.get()) is not None:
                yield json.dumps(event) + "\n"

        return StreamingResponse(
            gen(), media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def read_upload(request: Request, s: Services, file: UploadFile, target: str | None) -> bytes:
        if not s.allow(client_ip(request)):
            raise HTTPException(429, "Too many requests. Please wait a minute.")
        data = await file.read(s.settings.max_upload_bytes + 1)
        if len(data) > s.settings.max_upload_bytes:
            raise HTTPException(413, f"CSV too large (max {s.settings.max_upload_bytes // 1000} KB).")
        problem = validate_csv(data, target)
        if problem:
            raise HTTPException(422, problem)
        return data

    @app.get("/healthz")
    async def healthz(s: Dep) -> dict[str, object]:
        provider = resolve_provider(s.settings)
        ready = bool(
            (s.settings.vultr_api_key and s.settings.vultr_model)
            if provider == "vultr" else s.settings.gemini_api_key
        )
        return {
            "status": "ok", "provider": provider, "llm_ready": ready,
            "model": s.settings.vultr_model if provider == "vultr" else s.settings.gemini_model,
            "gvisor": s.settings.sandbox_runtime == "runsc",
            "limits": {"timeout_s": s.settings.sandbox_timeout_s, "memory": s.settings.sandbox_memory,
                       "max_upload_kb": s.settings.max_upload_bytes // 1000},
            "demo_cases": len(CASES),
        }

    @app.post("/api/analyze")
    async def analyze(
        request: Request, s: Dep, file: Annotated[UploadFile, File()],
        goal: Annotated[str, Form(max_length=500)] = "Explain what drives the target",
        target: Annotated[str | None, Form(max_length=100)] = None,
    ) -> JSONResponse:
        data = await read_upload(request, s, file, target or None)
        async with s.sem:
            try:
                out = await Orchestrator(s.llm, s.sandbox, s.settings).analyze(goal, target or None, data)
            except LLMError as exc:
                raise HTTPException(502, f"Model provider error: {exc}") from exc
        return JSONResponse(out.to_dict())

    @app.post("/api/analyze/stream")
    async def analyze_stream(
        request: Request, s: Dep, file: Annotated[UploadFile, File()],
        goal: Annotated[str, Form(max_length=500)] = "Explain what drives the target",
        target: Annotated[str | None, Form(max_length=100)] = None,
    ) -> StreamingResponse:
        data = await read_upload(request, s, file, target or None)

        async def work(emit: Emit) -> None:
            out = await Orchestrator(s.llm, s.sandbox, s.settings).analyze(
                goal, target or None, data, emit
            )
            await emit({"type": "final", **out.to_dict()})

        return stream(work)

    @app.get("/api/containment-demo")
    async def containment(request: Request, s: Dep) -> JSONResponse:
        if not s.allow(client_ip(request), DEMO_COST):
            raise HTTPException(429, "Too many requests. Please wait a minute.")
        async with s.sem:
            rows = await run_demo(s.sandbox)
        return JSONResponse({"all_contained": all(bool(r["contained"]) for r in rows), "results": rows})

    @app.get("/api/containment-demo/stream")
    async def containment_stream(request: Request, s: Dep) -> StreamingResponse:
        if not s.allow(client_ip(request), DEMO_COST):
            raise HTTPException(429, "Too many requests. Please wait a minute.")

        async def work(emit: Emit) -> None:
            ok = True
            await emit({"type": "plan", "attacks": [c.name for c in CASES]})
            async for row in iter_demo(s.sandbox):
                ok = ok and bool(row["contained"])
                await emit({"type": "case_result", **row})
            await emit({"type": "final", "all_contained": ok})

        return stream(work)

    if STATIC.exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def get_app() -> FastAPI:
    """uvicorn --factory app.main:get_app"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
