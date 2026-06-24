"""Shared response models for API endpoints."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationMeta(BaseModel):
    page: int = 1
    size: int = 20
    total: int = 0


class ApiResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: T | None = None
    pagination: PaginationMeta | None = None


def ok(data: Any = None, pagination: PaginationMeta | None = None) -> dict[str, Any]:
    resp: dict[str, Any] = {"code": 0, "message": "success", "data": data}
    if pagination:
        resp["pagination"] = pagination.model_dump()
    return resp


def error(code: int, message: str, detail: str = "") -> dict[str, Any]:
    return {"code": code, "message": message, "data": {"detail": detail} if detail else None}
