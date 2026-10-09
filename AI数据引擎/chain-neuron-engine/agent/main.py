# -*- coding: utf-8 -*-
"""Agent 启动入口：python -m agent.main"""
from __future__ import annotations

import uvicorn

from agent.python_agent.agent_server import app
from engine.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "agent.main:app",
        host="0.0.0.0",
        port=settings.AGENT_PORT,
        reload=False,
    )
