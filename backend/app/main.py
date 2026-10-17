import sys
from pathlib import Path

# Ensure repo root and backend are always in sys.path
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
_backend_dir = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import ipaddress
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app import db
from app.db import FalkorUnavailable
from app.auth import resolve_token
from app.graph.queries import sync_all
from app.ratelimit import auth_failures, http_limiter
from app.services import console_audit
from app.services.mcp_guard import record_event
from app.models.common import ErrorCode
from app.routes import (
    meta,
    graph,
    events,
    scenario,
    blast,
    handoffs,
    actions,
    agents,
    procedures,
    prompt_diff,
    brain,
    system,
    health,
    workspace as workspace_routes,
    mcp_info,
    catalog,
    observe,
    audit,
    session as session_routes,
    falkordb_proof,
    connect as connect_routes,
    work as work_routes,
)

_mcp_app = None
if os.getenv("QUORUM_EMBED_MCP") == "1":
    from app.mcp_embed import build_mcp_app

    _mcp_app = build_mcp_app()


def _flusher(stop: threading.Event) -> None:
    """Persists changes made by background work (agents, saga) that happen outside a request."""
    while not stop.wait(0.5):
        try:
            from app.graph.queries import _stores, _stores_lock, get_graph_store
            from app.services.saga import saga_worker

            # Discover persisted tenants too, so recovery does not wait for a user request after restart.
            for name in db.get_client().list_graphs():
                if name.startswith("quorum:"):
                    get_graph_store(name[len("quorum:"):])
            with _stores_lock:
                stores = list(_stores.values())
            for store in stores:
                saga_worker.process_pending(store, tenant=store.tenant)
            sync_all()
        except Exception:
            pass  # the next request reports FalkorDB being down; the failed diff is retried on the next pass


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    t = None
    if settings.PERSISTENCE == "falkordb":
        db.check_health(force=True)
        t = threading.Thread(target=_flusher, args=(stop,), daemon=True, name="quorum-flusher")
        t.start()
    try:
        if _mcp_app is not None:
            try:
                async with _mcp_app.router.lifespan_context(_mcp_app):
                    yield
            except RuntimeError as exc:
                if "can only be called once" in str(exc):
                    yield
                else:
                    raise
        else:
            yield
    finally:
        stop.set()
        if t is not None:
            t.join(timeout=2)


app = FastAPI(
    lifespan=lifespan,
    title="Quorum API",
    version=settings.CONTRACT_VERSION,
    description="Quorum Graph Memory & Multi-Agent Coordination Engine",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:8]}"
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = req_id
    return response


# POST routes that only read (a question needs a body, so it is a POST). They change nothing, so there is nothing to make durable:
# skipping the write-through sync for them is what lets a hub serve hundreds of retrievals a second.
_READ_ONLY_POSTS = ("/api/context/retrieve", "/api/work/announce", "/api/brain/ask", "/api/blast/")


def _is_read_only_post(path: str) -> bool:
    return path == "/api/events" or path.startswith(_READ_ONLY_POSTS)


def _is_open_path(path: str) -> bool:
    return path in ("/api/health", "/docs", "/openapi.json", "/redoc") or path.startswith("/docs/")


def _unavailable(reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": ErrorCode.FALKORDB_UNAVAILABLE.value,
                "message": "FalkorDB is not set up or not running. Quorum needs it and will not run without it.",
                "details": {"reason": reason, "url": db.redacted_url(), "steps": db.SETUP_STEPS},
            }
        },
        headers={"Retry-After": "3"},
    )


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (request.headers.get("x-quorum-token") or "").strip()


def _problem(status_code: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}}, headers=headers or {})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Headers every response carries: no sniffing, no framing, no referrer, and API answers are never cached."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith(("/api/", "/mcp")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _is_trusted_proxy(host: str) -> bool:
    if not host or host == "unknown":
        return False
    trusted = [p.strip() for p in (settings.TRUSTED_PROXIES or "").split(",") if p.strip()]
    if not trusted:
        return False
    try:
        ip = ipaddress.ip_address(host)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
    except ValueError:
        return False
    for t in trusted:
        try:
            if "/" in t:
                if ip in ipaddress.ip_network(t, strict=False):
                    return True
            else:
                tip = ipaddress.ip_address(t)
                if isinstance(tip, ipaddress.IPv6Address) and tip.ipv4_mapped is not None:
                    tip = tip.ipv4_mapped
                if ip == tip:
                    return True
        except ValueError:
            continue
    return False


def _client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    if _is_trusted_proxy(direct):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ips = [p.strip() for p in forwarded.split(",") if p.strip()]
            for candidate in reversed(ips):
                if not _is_trusted_proxy(candidate):
                    try:
                        ipaddress.ip_address(candidate)
                        return candidate
                    except ValueError:
                        continue
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            candidate = real_ip.strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
    return direct


@app.middleware("http")
async def edge_guard(request: Request, call_next):
    """
    Everything between the network and a route: body size cap, failed-sign-in lockout, rate limits, the Authorization
    header check on /mcp, and a record of what people do through the console.
    """
    path = request.url.path
    is_mcp = path == "/mcp" or path.startswith("/mcp/")
    if request.method == "OPTIONS" or not (is_mcp or path.startswith("/api/")) or _is_open_path(path):
        return await call_next(request)
    try:
        content_length = int(request.headers.get("content-length") or 0)
    except (ValueError, TypeError):
        content_length = 0
    if content_length > settings.MAX_BODY_BYTES:
        return _problem(413, "PAYLOAD_TOO_LARGE", f"Requests are limited to {settings.MAX_BODY_BYTES} bytes.")
    ip = _client_ip(request)
    wait = auth_failures.locked(ip)
    if wait:
        return _problem(429, "LOCKED_OUT", f"Too many failed sign-ins. Try again in {int(wait) + 1}s.", {"Retry-After": str(int(wait) + 1)})
    token = _bearer(request)
    limiter_key = token if (token and resolve_token(token)) else ip
    if path != "/api/events":
        ok, wait = http_limiter.allow(limiter_key)
        if not ok:
            return _problem(429, "RATE_LIMITED", f"Too many requests. Retry in {wait:.1f}s.", {"Retry-After": str(max(1, int(wait + 0.999)))})
    if is_mcp:
        if token:
            if not resolve_token(token):
                auth_failures.record(ip)
                return _problem(401, ErrorCode.UNAUTHENTICATED.value, "Invalid token in the Authorization header.", {"WWW-Authenticate": "Bearer"})
        elif settings.mcp_header_required():
            return _problem(401, ErrorCode.UNAUTHENTICATED.value, "This hub requires Authorization: Bearer <token> on /mcp.", {"WWW-Authenticate": "Bearer"})
    t0 = time.time()
    response = await call_next(request)
    if response.status_code == 401:
        auth_failures.record(ip)
    elif request.method != "GET" and response.status_code < 400 and token:
        _audit_console_action(request, token, (time.time() - t0) * 1000)
    return response


def _audit_console_action(request: Request, token: str, ms: float) -> None:
    """Writes by people (or the console) go into the same log as agent tool calls. Never lets a logging problem fail the request."""
    try:
        hit = console_audit.describe(request.method, request.url.path)
        info = resolve_token(token) if hit else None
        if hit and info:
            record_event(info, hit[0], hit[1], ms)
    except Exception:
        pass


@app.middleware("http")
async def falkordb_gate(request: Request, call_next):
    """Every API call needs FalkorDB. If it is down, nothing is served and the caller is told how to fix it."""
    path = request.url.path
    guarded = (path.startswith("/api/") or path.startswith("/mcp")) and not _is_open_path(path)
    if settings.PERSISTENCE != "falkordb" or not guarded or request.method == "OPTIONS":
        return await call_next(request)
    h = db.check_health()
    if not h["up"]:
        return _unavailable(h["error"] or "unreachable")
    try:
        response = await call_next(request)
        if request.method != "GET" and not _is_read_only_post(path):
            # Writes are durable before the caller hears "accepted".
            await run_in_threadpool(sync_all, getattr(request.state, "request_id", None))
        return response
    except FalkorUnavailable as e:
        return _unavailable(e.reason)
    except Exception as e:  # redis / falkordb driver errors mean the database went away mid-request
        if e.__class__.__module__.split(".")[0] in ("redis", "falkordb"):
            db.reset_client()
            db.check_health(force=True)
            return _unavailable(f"{type(e).__name__}: {e}"[:300])
        raise


# Standardized error responses matching docs/CONTRACT.md
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": ErrorCode.INTERNAL.value if exc.status_code == 500 else ErrorCode.VALIDATION.value,
                "message": str(exc.detail),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": ErrorCode.VALIDATION.value,
                "message": "Invalid request parameters or payload.",
                "details": {"errors": exc.errors()},
            }
        },
    )


# Mount routes under /api
api_prefix = settings.API_PREFIX

app.include_router(health.router, prefix=api_prefix, tags=["Health"])
app.include_router(workspace_routes.router, prefix=api_prefix, tags=["Workspace"])
app.include_router(mcp_info.router, prefix=api_prefix, tags=["MCP"])
app.include_router(catalog.router, prefix=api_prefix, tags=["MCP"])
app.include_router(observe.router, prefix=api_prefix, tags=["MCP"])
app.include_router(audit.router, prefix=api_prefix, tags=["MCP"])
app.include_router(session_routes.router, prefix=api_prefix, tags=["Session"])
app.include_router(falkordb_proof.router, prefix=api_prefix, tags=["FalkorDB"])
app.include_router(connect_routes.router, prefix=api_prefix, tags=["Connect"])
app.include_router(work_routes.router, prefix=api_prefix, tags=["Coordination"])
app.include_router(meta.router, prefix=api_prefix, tags=["Meta"])
app.include_router(graph.router, prefix=api_prefix, tags=["Graph"])
app.include_router(events.router, prefix=api_prefix, tags=["Events"])
app.include_router(scenario.router, prefix=api_prefix, tags=["Scenario"])
app.include_router(blast.router, prefix=api_prefix, tags=["Blast"])
app.include_router(handoffs.router, prefix=api_prefix, tags=["Handoffs"])
app.include_router(actions.router, prefix=api_prefix, tags=["Actions"])
app.include_router(agents.router, prefix=api_prefix, tags=["Agents"])
app.include_router(procedures.router, prefix=api_prefix, tags=["Procedures"])
app.include_router(prompt_diff.router, prefix=api_prefix, tags=["Prompt Diff"])
app.include_router(brain.router, prefix=api_prefix, tags=["Company Brain"])
app.include_router(system.router, prefix=api_prefix, tags=["System & Benchmarks"])


if _mcp_app is not None:
    from app.mcp_embed import mount

    mount(app, _mcp_app)


@app.get("/")
def root():
    return {
        "service": "Quorum API",
        "version": settings.CONTRACT_VERSION,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }
