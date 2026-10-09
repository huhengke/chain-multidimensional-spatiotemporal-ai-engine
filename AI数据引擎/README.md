# AI 数据引擎

Legacy AI 数据引擎实现，提供数据集注册、基座翻译、智慧树、精确聚合和 HTTP 查询入口。系统总体边界见 [产品架构与数据治理](../docs/产品架构与数据治理.md)，部署说明见 [部署运行与运维](../docs/部署运行与运维.md)。

## 目录

- `chain-neuron-engine/`：FastAPI 引擎、前端、测试和现有兼容代码。
- `数据源/`：外部 Excel 数据源；引擎通过 `CHAIN_SOURCE_DATA_ROOT` 读取，真实文件不纳入 Git。

## 独立运行

AI 数据引擎不依赖项目 Agent：

```powershell
$env:CHAIN_ENGINE_ROOT = (Resolve-Path ".\AI数据引擎\chain-neuron-engine").Path
$env:CHAIN_SOURCE_DATA_ROOT = (Resolve-Path ".\AI数据引擎\数据源").Path
$env:ENGINE_SERVICE_TOKEN = "高强度内部服务令牌"
Set-Location $env:CHAIN_ENGINE_ROOT
python -m engine.main
```

接口地址：`http://127.0.0.1:8000`。
