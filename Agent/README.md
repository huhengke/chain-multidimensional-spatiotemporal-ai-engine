# Agent

> Agent Gateway 通过 MCP 调用 AI 数据引擎，支持结构化图表、图片/PDF/Word/Excel 附件和受控 Skill 中心。系统边界与部署方式见 [产品架构与数据治理](../docs/产品架构与数据治理.md) 和 [部署运行与运维](../docs/部署运行与运维.md)。

独立 DeepSeek Harness Agent Gateway，通过 MCP 调用 AI 数据引擎。

## 目录

- `src/server.js`：会话、运行、SSE、取消和兼容 Chat API。
- `src/harness-runtime.js`：官方 `dsh headless --json` Runtime 适配。
- `src/mcp-server.js`：只读 AI 数据引擎 MCP Server。
- `src/skill-store.js`：受控 Skill 注册、校验、版本化、启停和归档。
- `src/skill-draft-generator.js`：复用当前模型通道生成待人工确认的 Skill 草稿。
- `presets/chain-neuron-headless.yml`：Harness headless Patch。
- `skills/`：可选项目 Skills；为空时通用 Agent 仍可工作。
- `tests/`：Gateway、Runtime 和 MCP 测试。

## 启动

```powershell
$env:ENGINE_BASE_URL = "http://127.0.0.1:8000"
$env:ENGINE_SERVICE_TOKEN = "高强度内部服务令牌"
$env:DEEPSEEK_API_KEY = "你的密钥"
node src/server.js
```

默认监听 `http://127.0.0.1:8001`。

## Skill 行为

- Agent 工作台右上角“Skills”进入 Skill 中心，可选择自动匹配或为当前会话指定加载。
- AI 生成结果先进入 `pending_review`，允许人工修改；只有确认发布后才写入启用目录。
- 匹配到 Skill：Harness 加载并执行标准流程；指定加载时只暴露本次勾选的 Skill。
- 没有相关 Skill：Harness 使用 MCP 发现引擎能力，动态规划并完成任务。
- 没有 Skill 不会被视为错误。
- Skill 更新、停用或归档后会重建会话 Harness 上下文，聊天记录仍保留。

## 文件权限边界

Harness Agent 默认保持只读，不能任意修改项目源码、配置、数据库或用户上传的文件。原因是附件和外部文档都属于不可信输入；在无人审批模式下直接开放文件与 Shell 写权限，会让提示注入具备修改系统的能力。

Skill 中心通过独立的 `SkillStore` 提供受控写入：

- 只允许写入 `Agent/skills`、停用目录、版本归档目录和临时运行目录。
- 名称、路径、内容和版本必须通过服务端校验，目录穿越会被拒绝。
- AI 只能生成草稿，人工确认后服务端才发布；旧版本先归档，删除操作也是可恢复归档。
- 业务查询仍只能通过只读 MCP 接口执行，Skill 不得绕过引擎直读数据库。

如后续确实需要 Agent 修改业务文件，应单独增加“可写任务模式”，并要求逐次授权、限定工作目录和保留审计记录，不应把它设为普通对话的默认权限。
