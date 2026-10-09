# Project Skills

> 当前启用 Skill 列表为空属于合法状态，通用 Agent 仍可通过 MCP 完成查询。Skill 中心的草稿、人工确认、版本化发布和可恢复归档边界见 [产品架构与数据治理](../../docs/产品架构与数据治理.md)。

此目录由 Harness Patch 注册为项目 Skill 目录。

- 可以按 DeepSeek Harness `SKILL.md` 规范添加可复用任务流程。
- Skill 是可选增强，不是查询 AI 数据引擎的必要条件。
- 目录中没有匹配 Skill 时，Harness 继续使用通用 Agent Planner 和 MCP 工具。
- Skill 不应直读 AI 数据引擎的 SQLite、Excel、JSON 或内部目录。
- 权威业务计算通过 `engine_query` 完成。
- 新 Skill 应先用通用 Agent 的成功运行记录验证，再经人工确认发布。
- 推荐在 Agent 工作台“Skill 中心”中生成、校验、修改和发布；不要让模型直接写此目录。
- 启用版本保存在本目录，停用版本和历史版本由 `SkillStore` 分目录管理；归档操作可恢复。
- 会话支持“自动匹配”和“指定加载”。指定加载使用隔离的临时 Skill 视图，不会把未勾选 Skill 暴露给本次 Harness 运行。
