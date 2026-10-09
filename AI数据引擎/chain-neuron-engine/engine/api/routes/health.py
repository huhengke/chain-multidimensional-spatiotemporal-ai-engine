# -*- coding: utf-8 -*-
"""健康检查。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from engine.core.config import DB_PATH

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "service": "chain-neuron-engine",
        "time": datetime.now().isoformat(timespec="seconds"),
        "db": "ok" if DB_PATH.exists() else "missing",
    }
