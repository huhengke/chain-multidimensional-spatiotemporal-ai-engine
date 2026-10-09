# -*- coding: utf-8 -*-
"""初始化默认账号（admin/operator/viewer）。已存在则跳过，不覆盖密码。"""
from __future__ import annotations

import sys
from pathlib import Path

# 定位工程根（scripts → root）并加入 sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.security.auth import hash_password
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, User
from engine.storage.database import SessionLocal, init_db

DEFAULT_USERS = [
    ("admin", "admin123", "系统管理员", ROLE_ADMIN),
    ("operator", "operator123", "运营操作员", ROLE_OPERATOR),
    ("viewer", "viewer123", "只读访客", ROLE_VIEWER),
]


def main() -> None:
    init_db()
    session = SessionLocal()
    try:
        created = 0
        for username, password, display, role in DEFAULT_USERS:
            exists = session.query(User).filter(User.username == username).one_or_none()
            if exists:
                continue
            salt, pwd_hash = hash_password(password)
            session.add(
                User(
                    username=username,
                    password_hash=pwd_hash,
                    password_salt=salt,
                    display_name=display,
                    role=role,
                    is_active=True,
                )
            )
            created += 1
        session.commit()
        print(f"默认账号初始化完成，新建 {created} 个（已存在则跳过）")
        print("  admin/admin123, operator/operator123, viewer/viewer123")
    finally:
        session.close()


if __name__ == "__main__":
    main()
