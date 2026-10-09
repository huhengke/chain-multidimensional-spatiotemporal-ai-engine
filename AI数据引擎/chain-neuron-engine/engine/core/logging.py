# -*- coding: utf-8 -*-
"""日志模块：控制台 + 按日轮转文件；支持 trace_id 串联跨服务调用。"""
from __future__ import annotations

import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler

from engine.core.config import LOG_DIR, get_settings

_CONFIGURED = False

# 日志格式
_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | trace=%(trace_id)s | %(message)s"


class _TraceFilter(logging.Filter):
    """为未注入 trace_id 的日志记录提供默认值。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = "-"
        return True


class _SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """多进程共享日志文件时，轮转失败不应污染业务输出。"""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            # 另一进程已经持有当天日志文件；跳过本次改名并安排下一次检查。
            self.rolloverAt = self.computeRollover(int(time.time()))


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, get_settings().LOG_LEVEL, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_FMT)
    trace_filter = _TraceFilter()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(trace_filter)
    root.addHandler(console)

    # 文件（按天轮转，保留 30 天；UTF-8）
    file_handler = _SafeTimedRotatingFileHandler(
        LOG_DIR / "engine.log", when="midnight", backupCount=30,
        encoding="utf-8", delay=True
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(trace_filter)
    root.addHandler(file_handler)

    # 降低第三方库噪音
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
