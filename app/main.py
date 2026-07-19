from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from app.api.routes import document_requests, documents, health
from app.config import settings
from app.core.logging import configure_logging
from app.core.rate_limit import limiter
from app.db.session import engine
from app.web.routes import router as web_router

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    lifespan=lifespan,
)

# Rate limiting (spec §15.4 — see app/core/rate_limit.py for defaults).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Health endpoints at root for operations probes.
app.include_router(health.router, tags=["health"])

# v1 surface defined in spec §6. The documents router carries its own /v1 prefix.
app.include_router(documents.router)
app.include_router(document_requests.router)

# Collection portal: staff pages, client upload pages, and local file serving.
# Same app, same port — no separate frontend server.
app.include_router(web_router)
