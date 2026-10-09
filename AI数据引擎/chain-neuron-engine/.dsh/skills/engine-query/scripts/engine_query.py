#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 执行器：只通过统一 HTTP API 查询 AI 数据引擎。"""
from __future__ import annotations

import json
import os
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "http://127.0.0.1:8000/api/v1/engine/query"


def main() -> int:
    try:
        payload = read_payload()
    except ValueError as exc:
        return emit_error(str(exc))
    if not isinstance(payload, dict):
        return emit_error("输入必须是 JSON 对象")

    url = os.environ.get("ENGINE_QUERY_URL", DEFAULT_URL)
    token = os.environ.get("ENGINE_SERVICE_TOKEN", "")
    trace_id = os.environ.get("ENGINE_TRACE_ID", str(uuid.uuid4()))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Service-Token": token,
            "X-Trace-Id": trace_id,
        },
    )
    try:
        with urlopen(request, timeout=float(os.environ.get("ENGINE_QUERY_TIMEOUT", "10"))) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return emit_error(f"AI 数据引擎 HTTP {exc.code}：{detail}")
    except (URLError, TimeoutError, OSError) as exc:
        return emit_error(f"无法连接 AI 数据引擎：{exc}")
    except json.JSONDecodeError as exc:
        return emit_error(f"AI 数据引擎返回的不是有效 JSON：{exc}")

    write_json(result)
    return 0


def read_payload() -> object:
    raw = sys.stdin.buffer.read()
    if not raw:
        raise ValueError("输入为空，需要一个 JSON 对象")
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError("输入不是有效 JSON：" + "; ".join(errors))


def write_json(value: object) -> None:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(data.encode("utf-8"))
    sys.stdout.buffer.flush()


def emit_error(message: str) -> int:
    write_json({"error": message})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
