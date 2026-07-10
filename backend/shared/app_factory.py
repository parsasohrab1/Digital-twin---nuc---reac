"""Shared FastAPI factory utilities."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from shared.config import Settings, get_settings


def setup_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.ndt_log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def create_service_app(
    title: str,
    lifespan: Callable | None = None,
) -> FastAPI:
    settings = get_settings()
    setup_logging(settings)

    @asynccontextmanager
    async def default_lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logging.getLogger(title).info("Starting %s on port %s", settings.service_name, settings.service_port)
        yield
        logging.getLogger(title).info("Shutting down %s", settings.service_name)

    app = FastAPI(
        title=title,
        version="1.0.0",
        description="Nuclear Digital Twin microservice",
        lifespan=lifespan or default_lifespan,
    )
    app.mount("/metrics", make_asgi_app())
    return app
