# -*- coding: utf-8 -*-
"""一次性脚本：按最小列集合重新注册 non_oil_sales 并 replace 导入，验证全量成功。

用法：python scripts/reregister_non_oil_sales.py
（引擎需已在 8000 端口运行；用完可删）
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
DATASET = "non_oil_sales"
FILE_TOKEN = "业务域乙线上商城业务_2024后.xlsx"

LEVELS = [
    {"key": "year", "label": "年", "derive": True, "key_of": "year"},
    {"key": "season", "label": "季节", "derive": True, "key_of": "season"},
    {"key": "month", "label": "月", "derive": True, "key_of": "yyyymm"},
    {"key": "store_name", "label": "服务单元名称", "source": "store_name"},
    {"key": "province_name", "label": "省份", "source": "province_name"},
    {"key": "city_name", "label": "城市", "source": "city_name"},
    {"key": "channel_name", "label": "渠道", "source": "channel_name"},
    {"key": "pay_mode", "label": "支付方式", "source": "pay_mode"},
]

METRICS = [
    ("total_amount", "记录总额"),
    ("real_amount", "实付金额"),
    ("convert_amount", "折算金额"),
    ("sale_amount", "业务金额"),
    ("cost_amount", "成本金额"),
    ("freight_amount", "运费"),
    ("freight_pay_amount", "运费支付"),
    ("freight_coupon_amount", "运费券抵扣"),
    ("freight_points_amount", "运费积分抵扣"),
    ("freight_points", "运费积分"),
    ("freight_discount_amount", "运费优惠"),
    ("coupon_amount", "券抵扣"),
    ("points_amount", "积分抵扣"),
    ("discount_amount", "总优惠"),
    ("pay_amount", "支付金额"),
    ("in_tax", "进项税"),
    ("sale_tax", "销项税"),
]

DRAFT = {
    "kind": "generic",
    "label": "业务域乙线上业务明细",
    "time": {"source": "pay_time", "format": "d/m/yyyy"},
    "levels": LEVELS,
    "metrics": [{"source": s, "name": s, "label": l} for s, l in METRICS],
}


def _req(method: str, path: str, token: str | None = None, body=None, form=None):
    url = BASE + path
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
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def urlencode_form(form: dict) -> bytes:
    from urllib.parse import urlencode
    return urlencode(form).encode("utf-8")


def main() -> None:
    st, tok = _req("POST", "/api/v1/auth/login",
                   body={"username": "admin", "password": "admin123"})
    assert st == 200, (st, tok)
    token = tok["access_token"]

    st, res = _req("PUT", f"/api/v1/engine/datasets/{DATASET}", token, body=DRAFT)
    print("register:", st, res.get("message") or res)
    assert st == 200, res

    st, res = _req("POST", f"/api/v1/engine/datasets/{DATASET}/import", token,
                   form={"mode": "replace", "file_token": FILE_TOKEN})
    print("import:", st, res)
    assert st == 200, res
    task = res["task_uuid"]

    for _ in range(120):
        time.sleep(2)
        st, t = _req("GET", f"/api/v1/engine/import/tasks/{task}", token)
        if t["status"] in ("success", "partial", "failed"):
            break
    print("task:", t["status"], "processed:", t.get("processed"),
          "succeeded:", t.get("succeeded"), "failed:", t.get("failed"))
    if t.get("error_sample"):
        print("errors:", json.dumps(t["error_sample"][:5], ensure_ascii=False))


if __name__ == "__main__":
    main()
