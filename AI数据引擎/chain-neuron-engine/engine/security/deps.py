# -*- coding: utf-8 -*-
"""FastAPI 鉴权依赖与公共依赖。

双层鉴权：
- 用户：Authorization: Bearer <JWT>（RBAC：admin/operator/viewer）；
- Agent/内部：X-Service-Token（服务令牌，全通道可用，不区分角色）。
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from engine.core.config import get_settings
from engine.core.errors import ErrorCode, AppException, ForbiddenError
from engine.security.auth import decode_access_token
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR, User
from engine.storage.database import get_session


def get_db() -> Session:  # 封装一层，便于统一替换
    yield from get_session()


def _get_user_by_username(session: Session, username: str) -> User | None:
    return session.query(User).filter(User.username == username).one_or_none()


def get_current_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppException(
            ErrorCode.UNAUTHORIZED[0],
            "缺少认证头 Authorization: Bearer <token>",
            http_status=401,
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    username = payload.get("sub")
    user = _get_user_by_username(session, username) if username else None
    if user is None or not user.is_active:
        raise AppException(
            ErrorCode.UNAUTHORIZED[0], "用户不存在或已停用", http_status=401
        )
    return user


def verify_service_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> str:
    settings = get_settings()
    if not x_service_token:
        raise AppException(
            ErrorCode.INVALID_SERVICE_TOKEN[0],
            "缺少内部服务令牌头 X-Service-Token",
            http_status=401,
        )
    if x_service_token != settings.ENGINE_SERVICE_TOKEN:
        raise AppException(
            ErrorCode.INVALID_SERVICE_TOKEN[0],
            "内部服务令牌无效",
            http_status=401,
        )
    return x_service_token


def allow_user_or_service(
    authorization: str | None = Header(default=None),
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    session: Session = Depends(get_db),
) -> dict:
    """双通道鉴权：任一通过即可。返回 {"principal": ..., "kind": "user|service"}。"""
    # 优先服务令牌
    if x_service_token:
        verify_service_token(x_service_token)
        return {"principal": "service", "kind": "service", "role": ROLE_OPERATOR}
    if authorization:
        user = get_current_user(authorization, session)
        return {"principal": user.username, "kind": "user", "role": user.role}
    raise AppException(
        ErrorCode.UNAUTHORIZED[0],
        "需要用户 JWT 或内部服务令牌",
        http_status=401,
    )


def require_roles(*roles: str) -> Callable:
    """角色限制依赖工厂。"""

    def _checker(identity: dict = Depends(allow_user_or_service)) -> dict:
        if identity["kind"] == "service":
            return identity  # 服务令牌全放行
        if identity["role"] not in roles:
            raise ForbiddenError(
                f"角色 {identity['role']} 无权执行此操作，需要：{','.join(roles)}"
            )
        return identity

    return _checker
