# -*- coding: utf-8 -*-
"""引擎启动入口：python -m engine.main"""
from __future__ import annotations

import uvicorn

from engine.api.app import create_app
from engine.core.config import get_settings

app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "engine.main:app",
        host="0.0.0.0",
        port=settings.ENGINE_PORT,
        reload=False,
    )
