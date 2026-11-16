from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from studio.api.routes import install_exception_handlers, register_routes
from studio.config import Settings
from studio.http_client import RequestsTransport
from studio.inference_client import InferenceClient


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or Settings.from_env()
    transport = transport or RequestsTransport()
    client = InferenceClient(settings, transport)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

    app = FastAPI(title="Multimodal Creative Studio", lifespan=lifespan)
    app.state.settings = settings
    app.state.client = client
    register_routes(app)
    install_exception_handlers(app)
    return app
