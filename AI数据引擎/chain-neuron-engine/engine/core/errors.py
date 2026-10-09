# -*- coding: utf-8 -*-
"""业务异常与错误码。

错误提示遵循偏好：明确中文提示 + 详细信息（detail），堆栈由日志层落盘。
"""
from __future__ import annotations


class ErrorCode:
    # 通用
    INTERNAL_ERROR = ("E5000", "系统内部错误")
    BAD_REQUEST = ("E4000", "请求参数错误")
    NOT_FOUND = ("E4040", "资源不存在")
    CONFLICT = ("E4090", "资源冲突")

    # 鉴权
    UNAUTHORIZED = ("E4010", "未认证或登录已过期")
    FORBIDDEN = ("E4030", "无权限执行该操作")
    INVALID_TOKEN = ("E4011", "无效的访问令牌")
    INVALID_SERVICE_TOKEN = ("E4012", "无效的内部服务令牌")

    # 数据导入
    IMPORT_FILE_ERROR = ("E4201", "导入文件无法读取")
    IMPORT_TASK_NOT_FOUND = ("E4202", "导入任务不存在")
    UNSUPPORTED_FILE = ("E4203", "不支持的文件类型")

    # 查询/路由
    ROUTE_NO_MATCH = ("E4301", "无匹配的路由节点")
    AGGREGATE_FORBIDDEN_DRILL = ("E4302", "聚合查询禁止下钻子分叉")
    FEATURE_PARSE_ERROR = ("E4303", "特征解析失败")

    # 数据源
    DATASOURCE_CONNECT_ERROR = ("E4401", "生产数据源连接失败")


class AppException(Exception):
    """业务异常基类。"""

    def __init__(
        self,
        code: str = ErrorCode.INTERNAL_ERROR[0],
        message: str = ErrorCode.INTERNAL_ERROR[1],
        detail: str | dict | list | None = None,
        http_status: int = 500,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.http_status = http_status

    def to_dict(self) -> dict:
        body = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class BadRequestError(AppException):
    def __init__(self, message: str = ErrorCode.BAD_REQUEST[1], detail=None):
        super().__init__(ErrorCode.BAD_REQUEST[0], message, detail, http_status=400)


class NotFoundError(AppException):
    def __init__(self, message: str = ErrorCode.NOT_FOUND[1], detail=None):
        super().__init__(ErrorCode.NOT_FOUND[0], message, detail, http_status=404)


class ConflictError(AppException):
    def __init__(self, message: str = ErrorCode.CONFLICT[1], detail=None):
        super().__init__(ErrorCode.CONFLICT[0], message, detail, http_status=409)


class UnauthorizedError(AppException):
    def __init__(self, message: str = ErrorCode.UNAUTHORIZED[1], detail=None,
                 code: str = ErrorCode.UNAUTHORIZED[0]):
        super().__init__(code, message, detail, http_status=401)


class ForbiddenError(AppException):
    def __init__(self, message: str = ErrorCode.FORBIDDEN[1], detail=None):
        super().__init__(ErrorCode.FORBIDDEN[0], message, detail, http_status=403)
