"""GET /health: comprueba que la API responde y que PostgreSQL acepta consultas."""

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import Engine, text

from app.db import get_engine

router = APIRouter()
log = logging.getLogger("crm.health")


def engine_dependency() -> Engine:
    return get_engine()


@router.get("/health")
def health(response: Response, engine: Engine = Depends(engine_dependency)) -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        # Sin detalles en la respuesta (podrían incluir el host o el usuario de la BD); solo el tipo en el log.
        log.warning("La base de datos no responde (%s).", type(e).__name__)
        response.status_code = 503
        return {"status": "error", "database": "no disponible"}
    return {"status": "ok", "database": "ok"}
