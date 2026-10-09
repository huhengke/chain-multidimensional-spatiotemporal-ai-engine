# Skill: aggregate_reader

## 职责
识别聚合意图，**直读中环**节点聚合值，禁止下钻到子分叉。

## 入参 schema
```json
{ "question": "string", "conditions": "object", "group_by": "string|null" }
```

## 调用的通道工具
- `engine_aggregations_query`

## 处理规则
1. 问“总量/合计/多少” → 不带 group_by，读取条件对应节点；
2. “各月/各组织层级一/各产品” → group_by = month / big_area / gas_code；
3. 到达目标层即停止，聚合值为 Decimal 字符串，原样引用。

## 输出
`{ nodes: [{ node_value, aggregations, record_count }] }`

## 约束
聚合值禁止改写、禁止参与任何二次运算；语言组织只能引用原值。
