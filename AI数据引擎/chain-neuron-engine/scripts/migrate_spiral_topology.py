# -*- coding: utf-8 -*-
"""将现有 spiral_nodes 回填为物理双链拓扑。

该脚本只更新拓扑派生列，不删除事实表、节点或数据集声明。
运行前建议由启动脚本停止写入服务并完成数据库备份。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.storage.database import init_db, session_scope
from engine.storage.topology import TopologyMigrator


def main() -> int:
    parser = argparse.ArgumentParser(description="migrate spiral_nodes to dual-chain topology")
    parser.add_argument("--dataset", action="append", dest="datasets", help="只迁移指定数据集，可重复")
    args = parser.parse_args()

    init_db()
    session = session_scope()
    try:
        result = TopologyMigrator(session).migrate(args.datasets)
        invalid = [item for item in result if not item.get("valid")]
        if invalid:
            session.rollback()
            print(json.dumps({"ok": False, "items": result}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({"ok": True, "items": result}, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
