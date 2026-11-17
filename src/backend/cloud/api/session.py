"""Routes of the Session tag of openapi/backend-server.yaml."""

from fastapi import APIRouter

from backend.cloud.api.common import not_built

router = APIRouter(tags=["Session"])


@router.post("/login")
def login():
    """C-10"""
    raise not_built("POST /login")


@router.post("/logout")
def logout():
    """C-10"""
    raise not_built("POST /logout")


@router.get("/me")
def me():
    """C-10"""
    raise not_built("GET /me")
