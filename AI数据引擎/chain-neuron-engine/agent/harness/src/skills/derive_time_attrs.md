# Skill: derive_time_attrs

## 职责
时间规则生成器：按用户确认的规则生成季节、季度、上下半年、单月/双月、周、节气和活动日等标准时间标签。

## 入参 schema
```json
{ "precision": "month|day", "year": "string", "month": "string" }
```

## 调用的通道工具
- `engine_derive_rules`

## 处理规则
1. precision=month：月份映射类规则生效；week/day/weekday_type/holiday/solar_term 一律 `null`（降级）；
2. precision=day：日期、星期、活动日和节气规则生效；
3. 规则变更走热加载与版本回滚，不停机。

## 输出
`{ season: string, quarter: string, half_year: string, month_parity: string, week: string, solar_term: string|null, holiday: string|null }`
