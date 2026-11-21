"""The server's routes are exactly the operations of openapi/backend-server.yaml."""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from backend.server.main import create_app

SPEC = Path(__file__).resolve().parents[2] / "openapi" / "backend-server.yaml"


def spec_operations():
    spec = yaml.safe_load(SPEC.read_text())
    return {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops if m in {"get", "post", "put", "patch"}}


def app_operations():
    paths = create_app().openapi()["paths"]
    return {(m.upper(), p) for p, ops in paths.items() for m in ops}


def test_routes_match_the_spec():
    assert app_operations() == spec_operations()


def test_errors_use_the_one_shape():
    r = TestClient(create_app()).get("/controls")  # no hub connected
    assert r.status_code == 503
    assert set(r.json()) == {"code", "message", "reason"}


def test_healthz_is_outside_the_spec():
    r = TestClient(create_app()).get("/healthz")
    assert r.status_code == 200
