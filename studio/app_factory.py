from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI

from studio.api.routes import install_exception_handlers, register_routes
from studio.config import Settings
from studio.database import init_db
from studio.http_client import RequestsTransport
from studio.providers.image.comfyui_h3_image import ComfyUIH3ImageProvider
from studio.providers.registry import ProviderRegistry
from studio.providers.video.comfyui_h3_video import ComfyUIH3VideoProvider
from studio.services.worker import worker_loop


def build_registry(settings: Settings) -> ProviderRegistry:
    return ProviderRegistry(
        [
            ComfyUIH3VideoProvider(settings, RequestsTransport()),
            ComfyUIH3ImageProvider(settings, RequestsTransport()),
        ]
    )


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    start_workers: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = registry or build_registry(settings)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ctx = SimpleNamespace(settings=settings, registry=registry, stop=threading.Event())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        settings.ensure_dirs()
        init_db(settings)
        ctx.stop.clear()
        threads = []
        if start_workers:
            for provider in registry.all():
                thread = threading.Thread(
                    target=worker_loop,
                    args=(settings, provider, ctx.stop),
                    name=f"studio-{provider.provider_id}",
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
        yield
        ctx.stop.set()
        for thread in threads:
            thread.join(timeout=2)

    app = FastAPI(title="Multimodal Creative Studio", lifespan=lifespan)
    register_routes(app, ctx)
    install_exception_handlers(app)
    return app
