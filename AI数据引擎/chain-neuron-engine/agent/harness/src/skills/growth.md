# Skill: growth

## 职责
判断节点是否达到生长阈值，触发分叉生长，读取上浮后的聚合结果。

## 入参 schema
```json
{ "dataset": "registered_dataset_id", "node_id": "string|null" }
```

## 调用的通道工具
- `engine_grow`
- `engine_spiral_tree`

## 处理规则
1. 默认阈值 10000 条/节点（env GROWTH_THRESHOLD 覆盖）；
2. 批量生长所有达阈值且未标记 grown 的节点；
3. 生长后读取螺旋树确认新分叉与上浮聚合。

## 输出
`{ grown: string[], tree: object }`
