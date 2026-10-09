# -*- coding: utf-8 -*-
"""认证路由：登录、当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from engine.api.schemas_api import LoginRequest, TokenResponse, UserInfo
from engine.core.errors import UnauthorizedError
from engine.security.auth import create_access_token, verify_password
from engine.security.deps import get_current_user, get_db
from engine.security.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_db)) -> TokenResponse:
    user = session.query(User).filter(User.username == body.username).one_or_none()
    if user is None or not verify_password(
        body.password, user.password_salt, user.password_hash
    ):
        raise UnauthorizedError("用户名或密码错误")
    if not user.is_active:
        raise UnauthorizedError("账号已停用，请联系管理员")

    token = create_access_token(user.username, extra={"role": user.role})
    return TokenResponse(
        access_token=token,
        user=UserInfo(
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        ),
    )


@router.get("/me", response_model=UserInfo)
def me(current: User = Depends(get_current_user)) -> UserInfo:
    return UserInfo(
        username=current.username,
        display_name=current.display_name,
        role=current.role,
    )
