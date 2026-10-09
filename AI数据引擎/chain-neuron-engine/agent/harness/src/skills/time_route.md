# Skill: time_route

## 职责
时间维度解析与路由：把用户的自然语言时间表达解析为 `year / season / month(YYYYMM) / quarter / half_year / month_parity`，
并在螺旋树上定位路径。

## 入参 schema
```json
{ "question": "string" }
```

## 调用的通道工具
- `engine_records_query`
- `engine_aggregations_query`

## 处理规则
1. “2024年” → `conditions.year = "2024"`；
2. “2024年3月 / 202401” → `conditions.month = "202403"`；
3. “春季/夏季/秋季/冬季” → `conditions.season`；
4. 月精度可以派生 season/quarter/half_year/month_parity，不得臆造周、节气、节日或工作日；这些必须要求日粒度。

## 输出
`{ conditions: object, route_path: array }`

## 约束
禁止自行对金额/数量做加减；数值一律取自引擎返回。
