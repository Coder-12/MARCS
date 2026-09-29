# api/app.py
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from api.routes.health import router as health_router
from api.routes.github import router as github_router
from core.db import init_db_pool, close_db_pool
from core.logging import configure_logging, get_logger

from api.routes.debug_queue import router as queue_debug_router
from api.routes.debug_worker import router as worker_debug_router
from api.routes.debug_eval import router as queue_eval_router
from api.routes.eval import router as eval_router
from api.routes.patch_eval import router as patch_eval_router
from core.tracing import with_new_trace, start_span, get_trace_id, get_span_id
from api.routes.review import router as review_router

import asyncio
from worker.worker import worker_loop, request_shutdown

from services.startup import run_startup_inspector, get_last_startup_decisions

from core.env_check import check_env, warn_env

import time

@asynccontextmanager
async def lifespan(app: FastAPI):
    check_env()
    warn_env()
    # Startup
    await init_db_pool()

    # Run startup inspector (synchronous, cheap) BEFORE starting workers
    # This will be a NOOP if inspector disabled via env.
    run_startup_inspector()

    # Start in-process background worker (Phase-0 convenience)
    worker_task = asyncio.create_task(worker_loop())

    try:
        yield
    finally:
        # signal worker to stop and wait for it
        request_shutdown()
        worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await worker_task

        # Shutdown
        await close_db_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="MACRS",
        description="Multi-Agent Code Review System (Phase 0 scaffold)",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Routers
    app.include_router(health_router)
    app.include_router(github_router)

    app.include_router(queue_debug_router)
    app.include_router(worker_debug_router)
    app.include_router(queue_eval_router)
    app.include_router(eval_router)
    app.include_router(patch_eval_router)


    app.include_router(review_router)

    # Structured request logging
    logger = get_logger("api")

    @app.middleware("http")
    async def trace_request_middleware(request, call_next):
        """
        - Accept `traceparent` (W3C) or `X-Request-Id`
        - Establish trace_id and a root span for this request
        - Bind logger automatically via structlog processor
        - Add response headers: X-Request-Id and traceparent
        """
        # try to extract w3c traceparent header if present
        traceparent = request.headers.get("traceparent")
        request_id = request.headers.get("X-Request-Id")

        # Accept incoming traceparent in format: version-traceid-span-flags
        if traceparent:
            try:
                # e.g. "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
                parts = traceparent.split("-")
                if len(parts) >= 3:
                    incoming_trace_id = parts[1]
                else:
                    incoming_trace_id = None
            except Exception:
                incoming_trace_id = None
            with_new_trace(incoming_trace_id)
        else:
            # create new trace
            with_new_trace(request_id or None)

        # Start a root span for the request
        with start_span("http.request") as span:
            # Attach request-level metadata to logs by binding to the logger (structlog will include trace)
            # Note: structlog processors will read from contextvars
            start_ts = time.time()
            response = await call_next(request)
            duration_ms = int((time.time() - start_ts) * 1000)
            # set response headers
            response.headers["X-Request-Id"] = get_trace_id()
            # set traceparent header
            response.headers["traceparent"] = f"00-{get_trace_id()}-{get_span_id()}-01"
            # add timing to logs
            get_logger("api").info("request_end", method=request.method, url=str(request.url), status=response.status_code, duration_ms=duration_ms)
            return response

    return app

app = create_app()