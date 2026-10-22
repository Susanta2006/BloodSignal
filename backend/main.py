"""FastAPI app factory, middleware and dependency wiring."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.agents.coordinator import Coordinator
from app.inventory.repository import InventoryRepository, MemoryInventoryRepository
from app.inventory.routes_v1 import router as inventory_router
from app.inventory.service import InventoryService
from app.orchestrator import Orchestrator
from app.provider import ModelProvider, build_provider
from app.routes import router
from app.voice import router as voice_router
from app.schemas import AppError, new_id, utcnow
from app.services.directory import (BankDirectory, DataGovDirectory, DemoDirectory, FallbackDirectory,
                                    GooglePlacesDirectory, UnconfiguredDirectory)
from app.services.eraktkosh import ERaktKoshAdapter
from app.services.geocoding import NominatimGeocoder
from app.services.routes import GoogleRoutes, NullRoutes, RoutesClient
from app.services.stock_source import DemoStockAdapter, StockSourceAdapter
from app.services.store import MemoryStore


@dataclass
class Container:
    settings: Settings
    store: MemoryStore
    orchestrator: Orchestrator
    provider: ModelProvider
    inventory: InventoryService
    agents: Coordinator
    metrics: dict[str, int] = field(default_factory=lambda: {"runs_created": 0, "http_5xx": 0})


def build_container(settings: Settings, *, stock: StockSourceAdapter | None = None,
                    directory: BankDirectory | None = None, routes: RoutesClient | None = None,
                    inventory_repo: InventoryRepository | None = None,
                    clock: Callable = utcnow,
                    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Container:
    live = not settings.demo_mode
    maps_key = settings.google_maps_server_key.get_secret_value()
    # Demo and live are separate code paths. Live mode never touches demo fixtures.
    if stock is None:
        stock = (DemoStockAdapter(clock) if not live else ERaktKoshAdapter(
            timeout_s=settings.stock_source_timeout_s,
            source_timezone=settings.eraktkosh_source_timezone or "Asia/Kolkata",
            clock=clock,
            geocoder=NominatimGeocoder(contact=settings.geocoder_contact,
                                       base_url=settings.geocoder_base_url,
                                       enabled=settings.geocoder_enabled,
                                       timeout_s=settings.geocoder_timeout_s),
            geocoder_candidate_limit=settings.geocoder_candidate_limit,
            geocoder_time_budget_s=settings.geocoder_time_budget_s))
    if directory is None:
        if settings.directory_source_mode == "demo":
            # Preserve fail-closed legacy live configuration; explicitly choose datagov
            # to use the government directory alongside either inventory mode.
            directory = (UnconfiguredDirectory() if live else DemoDirectory())
        elif settings.directory_source_mode == "datagov":
            primary = DataGovDirectory(settings)
            directory = (FallbackDirectory(primary, DemoDirectory())
                         if settings.datagov_fallback_to_demo else primary)
        elif settings.directory_source_mode == "google_places":
            directory = GooglePlacesDirectory(maps_key) if maps_key else UnconfiguredDirectory()
        else:
            directory = UnconfiguredDirectory()
    if routes is None:
        routes = GoogleRoutes(maps_key) if maps_key else NullRoutes()
    store = MemoryStore()
    if inventory_repo is None:
        # Deliberately process-local memory storage. No external database credentials or
        # External database integration is intentionally absent; cache state is lost on process restart.
        inventory_repo = MemoryInventoryRepository(clock)
    from app.services.demo_data import demo_banks_for
    inventory = InventoryService(settings=settings, adapter=stock, repo=inventory_repo,
                                 demo_banks=demo_banks_for, clock=clock, sleep=sleep)
    provider = build_provider(settings)
    orch = Orchestrator(settings=settings, store=store, directory=directory, stock=stock,
                        routes=routes, clock=clock, sleep=sleep)
    return Container(settings, store, orch, provider, inventory,
                     Coordinator(settings=settings, inventory=inventory, provider=provider, clock=clock))


def _error(request: Request, code: str, message: str, status: int, retryable=False, action="") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "retryable": retryable,
        "correlation_id": getattr(request.state, "correlation_id", None), "user_action": action}})


class MemoryRateLimiter:
    """Bounded single-process fallback for local development and tests."""
    def __init__(self, limit: int, max_clients: int = 10_000):
        self.limit, self.max_clients = limit, max_clients
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, client_key: str, now: float) -> bool:
        q = self._hits.pop(client_key, deque())
        while q and now - q[0] >= 60:
            q.popleft()
        allowed = len(q) < self.limit
        if allowed:
            q.append(now)
        self._hits[client_key] = q
        while len(self._hits) > self.max_clients:
            self._hits.popitem(last=False)
        return allowed


_REDIS_LIMIT_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return n
"""


class _BodyLimitExceeded(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Enforce limits while ASGI request chunks arrive, including chunked uploads."""
    def __init__(self, app, *, regular_limit: int, upload_limit: int):
        self.app, self.regular_limit, self.upload_limit = app, regular_limit, upload_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        limit = self.upload_limit if path == "/api/transcribe" else self.regular_limit
        total = 0
        response_started = False

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise _BodyLimitExceeded
            return message

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyLimitExceeded:
            if not response_started:
                body = (b'{"error":{"code":"PAYLOAD_TOO_LARGE",'
                        b'"message":"Request body too large.","retryable":false}}')
                await send({"type": "http.response.start", "status": 413, "headers": [
                    (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"), (b"referrer-policy", b"strict-origin-when-cross-origin")
                ]})
                await send({"type": "http.response.body", "body": body})


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level,
                        format='{"t":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    log = logging.getLogger("api")
    production = settings.app_env.lower() == "production"
    app = FastAPI(title="Pragmatrix Blood Availability Finder API", version="1.0.0",
                  docs_url=None if production else "/docs",
                  redoc_url=None if production else "/redoc",
                  openapi_url=None if production else "/openapi.json",
                  description="Evidence-first blood-bank search. Places/Routes never imply stock.")
    app.state.container = container or build_container(settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "Idempotency-Key", "X-Run-Token", "Authorization"],
                       expose_headers=["X-Request-ID", "Idempotent-Replay"])
    memory_limiter = MemoryRateLimiter(settings.rate_limit_per_minute)
    memory_voice_limiter = MemoryRateLimiter(settings.voice_rate_limit_per_minute)
    redis_client = None
    redis_state = {"unavailable": False, "retry_at": 0.0}
    if settings.rate_limit_redis_url:
        try:
            from redis.asyncio import Redis
            redis_client = Redis.from_url(settings.rate_limit_redis_url, socket_connect_timeout=1,
                                          socket_timeout=1, decode_responses=True)
        except ImportError:
            redis_state["unavailable"] = True
            if settings.app_env.lower() == "production":
                raise RuntimeError("RATE_LIMIT_REDIS_URL requires the redis package")
    app.state.rate_limit_redis = redis_client

    @app.middleware("http")
    async def guard(request: Request, call_next):
        supplied_id = request.headers.get("X-Request-ID", "")
        request.state.correlation_id = (supplied_id[:64] if supplied_id and
                                        all(ch.isalnum() or ch in "-_" for ch in supplied_id[:64])
                                        else new_id())
        start = time.monotonic()
        too_large = request.headers.get("content-length", "")
        body_limit = (settings.max_upload_body_bytes
                      if request.url.path == "/api/transcribe" else settings.max_request_body_bytes)
        try:
            body_size = int(too_large) if too_large else 0
        except ValueError:
            body_size = body_limit + 1
        if body_size > body_limit:
            response = _error(request, "PAYLOAD_TOO_LARGE", "Request body too large.", 413)
            return _security_headers(response, request)
        if request.method == "POST" and request.url.path.startswith("/api/"):
            ip = request.client.host if request.client else "unknown"
            if settings.trust_proxy_headers:
                forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
                if forwarded:
                    ip = forwarded
            client_key = hashlib.sha256(ip.encode("utf-8")).hexdigest()
            is_voice = request.url.path == "/api/transcribe"
            limit = settings.voice_rate_limit_per_minute if is_voice else settings.rate_limit_per_minute
            limiter = memory_voice_limiter if is_voice else memory_limiter
            scope = "voice:" if is_voice else "api:"
            allowed = None
            if redis_state["unavailable"] and time.monotonic() >= redis_state["retry_at"]:
                redis_state["unavailable"] = False
            if redis_client is not None and not redis_state["unavailable"]:
                try:
                    count = await redis_client.eval(_REDIS_LIMIT_SCRIPT, 1,
                                                    f"bloodsignal:rate:{scope}{client_key}")
                    allowed = int(count) <= limit
                except Exception:
                    redis_state["unavailable"] = True
                    redis_state["retry_at"] = time.monotonic() + 5
            if redis_state["unavailable"] and settings.app_env.lower() == "production":
                response = _error(request, "RATE_LIMIT_UNAVAILABLE",
                                  "Request protection is temporarily unavailable.", 503, True,
                                  "Try again shortly.")
                return _security_headers(response, request)
            if allowed is None:
                allowed = limiter.allow(client_key, time.monotonic())
            if not allowed:
                response = _error(request, "RATE_LIMITED", "Too many requests.", 429, True,
                                  "Wait a minute and try again.")
                return _security_headers(response, request)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.correlation_id
        _security_headers(response, request)
        if request.url.path.startswith("/api/") or request.url.path == "/metrics":
            response.headers["Cache-Control"] = "no-store"
        if response.status_code >= 500:
            app.state.container.metrics["http_5xx"] += 1
        # path + status only: never log bodies, tokens or coordinates
        log.info("%s %s %s %dms cid=%s", request.method, request.url.path, response.status_code,
                 int((time.monotonic() - start) * 1000), request.state.correlation_id)
        return response

    def _security_headers(response, request):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=(self)"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["X-Request-ID"] = request.state.correlation_id
        forwarded_https = (settings.trust_proxy_headers and
                           request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() == "https")
        if settings.app_env.lower() == "production" and (request.url.scheme == "https" or forwarded_https):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/") or request.url.path == "/metrics":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _error(request, exc.code, exc.message, exc.status_code, exc.retryable, exc.user_action)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
                  for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "Some fields are invalid.", "retryable": False,
            "correlation_id": request.state.correlation_id, "user_action": "Fix the listed fields.",
            "fields": fields}})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logging.getLogger("api").error("unhandled error_class=%s cid=%s", type(exc).__name__,
                                       getattr(request.state, "correlation_id", None))
        return _error(request, "INTERNAL_ERROR", "Something went wrong.", 500, True,
                      "Try again, or quote the correlation ID to support.")

    app.include_router(router)
    app.include_router(voice_router)
    app.include_router(inventory_router)
    app.add_middleware(RequestBodyLimitMiddleware,
                       regular_limit=settings.max_request_body_bytes,
                       upload_limit=settings.max_upload_body_bytes)
    return app


app = create_app()
