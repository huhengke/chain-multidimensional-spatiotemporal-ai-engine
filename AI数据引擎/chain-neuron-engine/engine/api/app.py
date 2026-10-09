# -*- coding: utf-8 -*-
"""FastAPI 应用组装：路由、异常处理、CORS。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engine.api.routes import (
    auth,
    datasets,
    datasources,
    features,
    foundation,
    grow,
    health,
    imports,
    model_config,
    params,
    query,
    reinitialize,
    runtime_profiles,
    rules,
    spiral,
    unified_query,
)
from engine.core.config import get_settings
from engine.core.errors import AppException
from engine.core.logging import configure_logging, get_logger
from engine.runtime_profiles.resolver import initialize_runtime_profiles
from engine.storage.database import init_db


def create_app() -> FastAPI:
    configure_logging()
    logger = get_logger("api")

    app = FastAPI(
        title="链式神经元多维时空 AI 数据引擎",
        description="数据引擎通道 API：Agent 层经标准 HTTP 通道调用，引擎不内置 LLM。",
        version="1.0.0",
    )

    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(query.router)
    app.include_router(unified_query.router)
    app.include_router(features.router)
    app.include_router(foundation.router)
    app.include_router(model_config.router)
    app.include_router(reinitialize.router)
    app.include_router(spiral.router)
    app.include_router(imports.router)
    app.include_router(grow.router)
    app.include_router(rules.router)
    app.include_router(datasets.router)
    app.include_router(datasources.router)
    app.include_router(params.router)
    app.include_router(runtime_profiles.router)

    # 异常处理
    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        logger.warning("业务异常 %s: %s (%s)", exc.code, exc.message, exc.detail)
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request,
                                exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "E4000",
                "message": "请求参数校验失败",
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """未捕获异常：堆栈落盘（engine.log）+ 返回友好 JSON，不裸奔 500。"""
        logger.exception("未捕获异常 %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": "E5000",
                "message": "系统内部错误，请联系管理员（详见引擎日志）",
                "detail": {"error": f"{type(exc).__name__}: {exc}"[:300],
                           "path": request.url.path},
            },
        )

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        profile = initialize_runtime_profiles()
        logger.info(
            "活动运行配置档 profile=%s version=%d storage=%s catalog=%s",
            profile.profile_id,
            profile.profile_version,
            profile.storage.type,
            profile.catalog.catalog_id,
        )
        logger.info("引擎启动完成 port=%d", settings.ENGINE_PORT)

    return app
