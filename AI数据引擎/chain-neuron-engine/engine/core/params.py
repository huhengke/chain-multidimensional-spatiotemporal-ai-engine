# -*- coding: utf-8 -*-
"""系统参数：界面可修改的运行参数（data/system_params.yaml，热读取）。

优先级：界面文件 > 环境变量 > 代码默认。
文件按 mtime 热加载，改参无需重启；生长阈值类参数对"下次导入/rebuild/生长"生效。
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any

import yaml

from engine.core.config import DATA_DIR, get_settings

PARAMS_FILE = DATA_DIR / "system_params.yaml"
PARAMS_SNAPSHOT_DIR = DATA_DIR / "param_snapshots"

_lock = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "values": {}}


def _specs() -> dict[str, dict]:
    s = get_settings()
    return {
        "growth_threshold": {
            "label": "螺旋生长阈值（条）",
            "description": "节点记录数达到该阈值才向下生长一层；导入/rebuild 后按此修剪。",
            "type": "int", "min": 100, "max": 1000000,
            "default": s.DEFAULT_GROWTH_THRESHOLD,
            "env": "GROWTH_THRESHOLD",
        },
        "import_batch_size": {
            "label": "导入批大小（行）",
            "description": "批量导入每批提交的事实行数。",
            "type": "int", "min": 100, "max": 50000,
            "default": s.IMPORT_BATCH_SIZE,
            "env": "IMPORT_BATCH_SIZE",
        },
    }


def _coerce(spec: dict, raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"必须是 {spec['min']}~{spec['max']} 的整数")
    if value < spec["min"] or value > spec["max"]:
        raise ValueError(f"超出允许范围 {spec['min']}~{spec['max']}")
    return value


def _file_values() -> dict[str, int]:
    if not PARAMS_FILE.exists():
        return {}
    with PARAMS_FILE.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("params", data) if isinstance(data, dict) else {}


def _values() -> dict[str, int]:
    mtime = PARAMS_FILE.stat().st_mtime if PARAMS_FILE.exists() else None
    with _lock:
        if _cache["mtime"] != mtime or not _cache["values"]:
            merged = {k: spec["default"] for k, spec in _specs().items()}
            merged.update({k: v for k, v in _file_values().items() if k in merged})
            _cache.update(mtime=mtime, values=merged)
        return dict(_cache["values"])


def get_param(name: str) -> int:
    spec = _specs().get(name)
    if spec is None:
        raise KeyError(f"未知系统参数：{name}")
    return _values()[name]


def list_params() -> list[dict]:
    values = _values()
    file_keys = set(_file_values())
    out = []
    for key, spec in _specs().items():
        env_name = spec.get("env")
        source = ("interface" if key in file_keys
                  else "env" if env_name and os.environ.get(env_name) is not None
                  else "default")
        out.append({
            "key": key, "label": spec["label"], "description": spec["description"],
            "type": spec["type"], "min": spec["min"], "max": spec["max"],
            "value": values[key], "default": spec["default"], "source": source,
        })
    return out


def update_params(raw: dict[str, Any]) -> dict[str, int]:
    """校验 → 快照存档 → 原子落盘。返回生效值。"""
    specs = _specs()
    unknown = [k for k in raw if k not in specs]
    if unknown:
        raise ValueError(f"未知系统参数：{unknown}")
    current = _file_values()
    merged = {k: current.get(k, specs[k]["default"]) for k in specs}
    for key, value in raw.items():
        merged[key] = _coerce(specs[key], value)

    PARAMS_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if PARAMS_FILE.exists():
        snaps = sorted(PARAMS_SNAPSHOT_DIR.glob("system_params_v*.yaml"))
        version = len(snaps) + 1
        shutil.copy2(PARAMS_FILE, PARAMS_SNAPSHOT_DIR / f"system_params_v{version}.yaml")
    tmp = Path(str(PARAMS_FILE) + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"params": merged}, f, allow_unicode=True, sort_keys=False)
    tmp.replace(PARAMS_FILE)
    with _lock:
        _cache.update(mtime=None, values={})  # 强制下次重读
    return merged
