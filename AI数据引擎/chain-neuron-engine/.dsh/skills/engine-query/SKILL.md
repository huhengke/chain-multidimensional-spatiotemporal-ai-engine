---
name: engine-query
description: 使用统一 AI 数据引擎查询入口，先做时间、产品、组织和数据可用性确定性解析，再返回业务或计划结果
user-invocable: true
disable-model-invocation: false
---

# 统一 AI 数据引擎查询

适用于业务、计划、实际与计划对比、数量、金额、吨数、升数以及“本月/上月/某月”问题。

## 执行规则

1. 不直接访问 SQLite、Excel、JSON 或数据引擎内部文件。
2. 不调用明细结果后在模型侧求和。
3. 通过统一查询执行器调用 `POST /api/v1/engine/query`。
4. 请求使用结构化 JSON，时间优先传 `current_month` 或 `YYYYMM`。
5. 用户说“类别甲”等产品类别时，让执行器通过基础目录映射为数据集对应的产品编码。
6. 用户说组织简称时，让执行器通过组织映射得到各数据集的真实组织条件。
7. 返回结果中的金额、吨数、升数和记录数必须原样引用，不由模型加减。
8. “无数据”不能改写为“数量为 0”。
9. 必须展示查询月份、单位、数据最新月份和警告。

## 标准请求

```json
{
  "intent": "actual_vs_plan",
  "period": "current_month",
  "location": "示例区域甲",
  "product": "类别甲",
  "metrics": ["actual_qty_ton", "plan_qty_ton"]
}
```

## 执行器

Skill 包内的 `scripts/engine_query.py` 只通过 HTTP API 调用引擎：

```text
python scripts/engine_query.py --stdin-json
```

当前 DeepSeek Harness 应通过受控工具调用该执行器或调用同一统一查询能力；禁止让模型自行执行任意 shell 命令。
