"""Kasa Health API. README.md explains how the pieces fit together."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .config import Settings, settings as default_settings
from .errors import install_error_handlers
from .middleware import RequestContextMiddleware
from .routes import admin as admin_routes
from .routes import ai as ai_routes
from .routes import feedback as feedback_routes
from .services import Services


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every URL at INFO, which is noisy, so I turn it down.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None,
               transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or default_settings
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = Services.create(settings, transport)
        app.state.services = services
        await services.start()
        try:
            yield
        finally:
            await services.stop()

    app = FastAPI(
        title="Kasa Health", version="8.0.0", lifespan=lifespan,
        # The /docs page is handy locally; I switch it off in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None, openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Request-ID"])
    app.add_middleware(RequestContextMiddleware, settings=settings)
    install_error_handlers(app)

    app.include_router(admin_routes.public)
    app.include_router(ai_routes.router)
    app.include_router(feedback_routes.router)
    app.include_router(admin_routes.admin)
    return app


app = create_app()
