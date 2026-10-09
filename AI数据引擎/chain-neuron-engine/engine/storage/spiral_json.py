# -*- coding: utf-8 -*-
"""螺旋 JSON 文件路径与原子写入。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from engine.core.config import SPIRAL_DIR
from engine.storage.models import SpiralNodeORM


def node_json_path(node: SpiralNodeORM) -> Path:
    """根据 node_id（dataset:v1/v2/...）映射文件路径：

    data/spiral/<dataset>/<v1>/<v2>/.../node.json
    """
    head, _, part = node.node_id.partition(":")
    values = [v for v in part.split("/") if v and v != "root"]
    return SPIRAL_DIR / head / Path(*values) / "node.json" if values else SPIRAL_DIR / head / "node.json"


def write_node_json(path: Path, payload: dict) -> None:
    """原子写入：同目录临时文件 + os.replace；保证不出现半文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=".node-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def purge_dataset_json(dataset: str) -> int:
    """删除一个数据集的物化螺旋 JSON，并返回删除文件数。

    仅允许删除 ``data/spiral/<dataset>`` 的直接子目录，避免注销流程
    因错误数据集标识误删 spiral 根目录或其他路径。
    """
    if not dataset or Path(dataset).name != dataset or dataset in {".", ".."}:
        raise ValueError(f"非法数据集标识：{dataset}")
    root = SPIRAL_DIR.resolve()
    target = (SPIRAL_DIR / dataset).resolve()
    if target.parent != root:
        raise ValueError(f"螺旋目录越界：{dataset}")
    if not target.exists():
        return 0
    files = sum(1 for path in target.rglob("*") if path.is_file())
    shutil.rmtree(target)
    return files


def purge_all_dataset_json() -> dict[str, int]:
    """清理 spiral 根目录下全部数据集物化目录，保留根目录本身。"""
    root = SPIRAL_DIR.resolve()
    removed: dict[str, int] = {}
    if not root.exists():
        return removed
    for child in root.iterdir():
        if not child.is_dir() or child.parent.resolve() != root:
            continue
        removed[child.name] = purge_dataset_json(child.name)
    return removed
