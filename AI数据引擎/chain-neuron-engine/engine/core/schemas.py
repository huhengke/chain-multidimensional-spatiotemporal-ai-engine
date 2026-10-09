# -*- coding: utf-8 -*-
"""通用 Pydantic 模型与统一响应包装。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应结构。"""

    code: str = "0"
    message: str = "success"
    data: T | None = None
    trace_id: str | None = None

    @classmethod
    def ok(cls, data: Any = None, message: str = "success", trace_id: str | None = None):
        return cls(code="0", message=message, data=data, trace_id=trace_id)


class PageResult(BaseModel, Generic[T]):
    total: int
    page: int
    page_size: int
    items: list[T]


class ORMModel(BaseModel):
    """支持从 SQLAlchemy ORM 对象转换。"""

    model_config = ConfigDict(from_attributes=True)
