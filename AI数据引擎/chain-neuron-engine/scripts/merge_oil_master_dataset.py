"""产品主数据去重（#45）：恢复基座 oil_master 并按 MaterialCode 粒度重新导入。

背景：同一份 示例产品基础信息.xlsx 有两份声明——基座 oil_master（primary_key=MaterialCode，
已注销）与向导注册的 oil_product_master（primary_key=TypeCode，只落了 5 个类别行）。
`engine/features/registry.py::_oil_master()` 取的是前者，因此 product_crosswalk 一直
拿不到主数据名称/类别代码。本脚本只做增量：恢复基座声明对应的维表并导入源文件。

用法：python scripts/merge_oil_master_dataset.py [--dry-run]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
DATASET = "oil_master"
SOURCE_NAME = "示例产品基础信息.xlsx"
EXPECT_ROWS = 44


def _req(method: str, path: str, token: str = "", body: dict | None = None,
         form: dict[str, str] | None = None) -> tuple[int, object]:
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    elif form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, raw


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    st, res = _req("POST", "/api/v1/auth/login",
                   body={"username": "admin", "password": "admin123"})
    assert st == 200, (st, res)
    token = res["access_token"]

    st, res = _req("GET", f"/api/v1/engine/datasets/{DATASET}", token)
    print(f"[现状] GET {DATASET}: {st} "
          f"{'disabled=' + str(res.get('disabled')) if st == 200 else str(res)[:160]}")

    if dry_run:
        print("[dry-run] 未提交")
        return 0

    st, res = _req("POST", f"/api/v1/engine/datasets/{DATASET}/restore", token, body={})
    print(f"[恢复] {st}: {res.get('message') if st == 200 else str(res)[:200]}")
    if st != 200:
        return 1
    print("       tables:", res.get("tables"))

    st, res = _req("POST", f"/api/v1/engine/datasets/{DATASET}/import", token,
                   form={"mode": "replace", "file_token": SOURCE_NAME})
    print(f"[导入] {st}: {res if st == 200 else str(res)[:200]}")
    if st != 200:
        return 1
    task = res["task_uuid"]

    for _ in range(120):
        time.sleep(1.0)
        st, info = _req("GET", f"/api/v1/engine/import/tasks/{task}", token)
        if st == 200 and info.get("status") not in ("pending", "running"):
            print("[任务] ", {k: info.get(k) for k in
                            ("status", "total_rows", "processed_rows", "error_message")})
            break
    else:
        print("[任务] 超时未结束")
        return 1

    sys.path.insert(0, str(ROOT))
    from engine.storage.database import session_scope
    from sqlalchemy import text
    with session_scope() as s:
        n = s.execute(text(f"select count(*) from dim_oil_master")).scalar()
        cols = [c[1] for c in s.execute(text("pragma table_info(dim_oil_master)"))]
        print(f"[校验] dim_oil_master 行数={n}（期望 {EXPECT_ROWS}）列={cols}")
        for row in s.execute(text(
            "select material_code, material_name, type_name from dim_oil_master "
            "order by material_code limit 3")):
            print("       ", tuple(row))
        print("[crosswalk] ", s.execute(text(
            "select status, count(*) from product_crosswalk group by status")).all())
        print("[crosswalk 有名称] ", s.execute(text(
            "select count(*) from product_crosswalk "
            "where canonical_name is not null and canonical_name <> ''")).scalar())
    return 0


if __name__ == "__main__":
    sys.exit(main())
