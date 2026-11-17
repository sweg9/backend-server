"""Shared pieces of the routers: the one error shape of the API conventions."""

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, reason: str | None = None):
        super().__init__(status, {"code": code, "message": message, "reason": reason})


def not_built(what: str) -> ApiError:
    return ApiError(501, "not_built", f"{what} is not built yet", "This is the backend skeleton.")
