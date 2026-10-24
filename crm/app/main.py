"""API del CRM de JS CONSULTORES Y ABOGADOS (Fase 1: /health). Solo se publica en 127.0.0.1:8000."""

import logging

from fastapi import FastAPI, Request

from app.api import health
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


def create_app() -> FastAPI:
    dev = get_settings().env == "dev"
    app = FastAPI(
        title="CRM JS CONSULTORES Y ABOGADOS",
        version="0.1.0",
        docs_url="/docs" if dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if dev else None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if not (dev and request.url.path in ("/docs", "/openapi.json")):
            response.headers.update(SECURITY_HEADERS)
        return response

    app.include_router(health.router)
    return app


app = create_app()
