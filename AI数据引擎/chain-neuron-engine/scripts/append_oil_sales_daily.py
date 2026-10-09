# -*- coding: utf-8 -*-
"""向日粒度数据集 oil_sales_daily 追加导入根目录的定位单元级业务域甲日报文件。

用法（引擎需在 8000 端口运行，文件已放进 data/blobs/）：
  python scripts/append_oil_sales_daily.py 定位单元级业务域甲日报_6月7月.xlsx 定位单元级业务域甲日报_8月9月.xlsx
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
DATASET = "oil_sales_daily"


def _req(method, path, token=None, body=None, form=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode_form(form)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def urlencode_form(form):
    from urllib.parse import urlencode
    return urlencode(form).encode("utf-8")


def wait_task(token, task_uuid):
    t = {}
    for _ in range(900):
        time.sleep(2)
        _, t = _req("GET", f"/api/v1/engine/import/tasks/{task_uuid}", token)
        if t.get("status") in ("success", "partial", "failed"):
            return t
    return t


def main():
    files = [a for a in sys.argv[1:] if a.endswith(".xlsx")]
    if not files:
        raise SystemExit("请给出 data/blobs/ 内的 xlsx 文件名")
    _, tok = _req("POST", "/api/v1/auth/login",
                  body={"username": "admin", "password": "admin123"})
    token = tok["access_token"]

    _, before = _req("GET", f"/api/v1/engine/datasets/{DATASET}/levels/year/values", token)
    print("导入前节点：", json.dumps(before, ensure_ascii=False)[:200])

    for name in files:
        st, res = _req("POST", f"/api/v1/engine/datasets/{DATASET}/import", token,
                       form={"mode": "append", "file_token": name})
        print(f"[{name}] import:", st, res.get("task_uuid"))
        t = wait_task(token, res["task_uuid"])
        print(f"[{name}] task:", t.get("status"), "processed:", t.get("processed"),
              "succeeded:", t.get("succeeded"), "failed:", t.get("failed"))
        if t.get("error_sample"):
            print(f"[{name}] errors:", json.dumps(t["error_sample"][:3], ensure_ascii=False))
        if t.get("status") != "success":
            raise SystemExit(f"{name} 导入未成功，停止后续文件以免混入半截数据")


if __name__ == "__main__":
    main()
