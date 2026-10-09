# -*- coding: utf-8 -*-
"""认证基础：PBKDF2 密码哈希、JWT 签发/解析（仅标准库 + PyJWT）。"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from engine.core.config import get_settings
from engine.core.errors import ErrorCode, AppException

_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 200_000


# ---------------- 密码 ----------------

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (salt_hex, hash_hex)。"""
    salt_bytes = bytes.fromhex(salt) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO,
        password.encode("utf-8"),
        salt_bytes,
        _PBKDF2_ITERATIONS,
    )
    return salt_bytes.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    _, candidate = hash_password(password, salt_hex)
    return hmac.compare_digest(candidate, expected_hash_hex)


# ---------------- JWT ----------------

def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise AppException(
            ErrorCode.UNAUTHORIZED[0], "登录已过期，请重新登录", http_status=401
        )
    except jwt.InvalidTokenError:
        raise AppException(
            ErrorCode.INVALID_TOKEN[0], "无效的访问令牌", http_status=401
        )
