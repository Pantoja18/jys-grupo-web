"""GET /health."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.health import engine_dependency
from app.main import create_app


def client_with(engine):
    app = create_app()
    app.dependency_overrides[engine_dependency] = lambda: engine
    return TestClient(app)


def test_health_ok_con_la_bd(db):
    r = client_with(db.app_engine).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]


def test_health_503_sin_bd_y_sin_detalles():
    engine = create_engine("postgresql+psycopg://usuario:clave-secreta@127.0.0.1:1/jys",
                           connect_args={"connect_timeout": 2})
    r = client_with(engine).get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "error", "database": "no disponible"}
    assert "clave-secreta" not in r.text and "127.0.0.1" not in r.text


def test_sin_documentacion_interactiva_fuera_de_dev():
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
