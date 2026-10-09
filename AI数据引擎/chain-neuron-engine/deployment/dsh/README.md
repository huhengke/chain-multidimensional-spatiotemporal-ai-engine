# DeepSeek Harness 部署模板

> 现行 Agent Gateway 位于顶级 `Agent/`，本目录作为部署模板保留。当前端口、查询契约、附件与 Skill 权限边界见 [部署运行与运维](../../../../docs/部署运行与运维.md)。

## 当前查询架构

Harness 默认使用一个统一的 `engine_query` 工具；该工具调用 FastAPI 的：

```text
POST /api/v1/engine/query
```

项目 Skill 位于 `chain-neuron-engine/.dsh/skills/engine-query/`，其中的 Python 执行器只通过 HTTP API 访问引擎，不访问数据库、Excel 或内部文件。旧的 daily/plan 螺旋树接口保留用于兼容和调试。

## 安装 preset

将 `agent-presets/chain-neuron/` 复制到：

```text
$env:DSH_HOME\.agent-presets\chain-neuron\
```

如果没有设置 `DSH_HOME`，按当前 DeepSeek Harness 的默认用户目录处理。

## 启动前设置

```powershell
$env:ENGINE_SERVICE_TOKEN = "实际服务令牌"
```

然后在项目根目录运行：

```powershell
.\chain-neuron-engine\deployment\dsh\start-harness.ps1
```

脚本会设置 `CHAIN_ENGINE_ROOT`、`CHAIN_PROJECT_ROOT` 和 `ENGINE_BASE_URL`，并让 DSH preset 通过 stdio 启动 `mcp-server.js`。默认仅向模型暴露健康检查、统一查询和明细查询。

## 说明

- MCP Server 首期只暴露只读工具；统一查询工具调用 HTTP API `/api/v1/engine/query`。
- FastAPI 引擎必须先运行在 `ENGINE_BASE_URL` 指定的地址。
- Skill Python 执行器使用 `ENGINE_QUERY_URL` 和 `ENGINE_SERVICE_TOKEN` 环境变量。
- 服务令牌通过 preset 的 `env` 显式注入 MCP 子进程。
- 不要修改 DSH 安装目录中的 shipped preset。
