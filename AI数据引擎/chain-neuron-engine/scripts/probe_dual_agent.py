# -*- coding: utf-8 -*-
"""双 Agent 答案并排比对（#30 数据集路由与批量渲染取证）。

用法：PYTHONIOENCODING=utf-8 python scripts/probe_dual_agent.py [问题序号...]
"""
from __future__ import annotations

import json
import sys
import urllib.request

PORTS = {"python": 8001, "harness": 8002}
TOKEN = "dev-service-token-please-rotate"

QUESTIONS = [
    "2025年业务域乙示例定位单元丙的业务记录有哪些？",
    "2024年各月业务数量如何？",
    "2024年总业务金额是多少？",
    "上周各组织层级一的业务数据甲数量",
    "2024年示例区域甲组织层级一的业务数据甲金额是多少？",
    "2025年示例区域乙组织层级三业务数据乙金额",
    "2024年业务金额同比增长多少？",
    "2026年4月1日业务数据甲金额是多少？",
    "2024年示例定位单元丙的业务记录有哪些？",
    "去年8月示例区域甲组织层级一的业务金额是多少？",
    # 类别代码/品类：两套都必须只下传基座产品词，由主数据按真实取值落层
    "2025年类别代码甲业务金额是多少？",
    "2024年类别甲的业务金额是多少？",
    "2024年类别代码乙业务金额是多少？",
    # 无锚点文本：两套都必须回同一段澄清文案
    "哈哈哈",
    "今天天气怎么样",
]


def ask(port: int, message: str) -> dict:
    body = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/chat",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Service-Token": TOKEN,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def main() -> None:
    picked = [int(v) for v in sys.argv[1:]] or list(range(len(QUESTIONS)))
    for index in picked:
        question = QUESTIONS[index]
        print("=" * 90)
        print(f"[{index}] {question}")
        answers = {}
        for name, port in PORTS.items():
            try:
                data = ask(port, question)
            except Exception as error:  # 服务未起时如实报，不猜
                print(f"--- {name}: 调用失败 {error}")
                continue
            intent = data.get("intent") or {}
            answers[name] = data.get("answer") or ""
            print(
                f"--- {name} | dataset={intent.get('dataset')} "
                f"datasets={intent.get('datasets')} scope={intent.get('dataset_scope')} "
                f"comparisons={intent.get('comparisons')}"
            )
            print(answers[name].strip())
        if len(answers) == 2:
            print("=== " + ("一致" if answers["python"] == answers["harness"] else "不一致"))
        else:
            print("=== 无法判定")


if __name__ == "__main__":
    main()
