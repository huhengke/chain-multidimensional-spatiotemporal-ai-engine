# 自研 AI 数据引擎

这是与 Legacy SQLite 物理存储隔离的 CNTS（Chain-Neuron Tree Storage）实现。CNTS 已完成原生二进制格式、五数据集不可变 Catalog、语义资产绑定、统一查询适配器和运行配置档控制面；旧系统 API 与 Agent 通道保持不变，由管理员通过完整运行配置档决定使用 Legacy 还是 CNTS。

候选配置档的历史验收记录已收敛到 [验收、迁移与演进](../docs/验收、迁移与演进.md)。本发布副本不包含运行时配置和数据，因此不声明任何环境的活动引擎。系统不会自动切换，也不会在 CNTS 故障时回退 Legacy；切换必须由管理员在“AI 引擎与数据集”页面明确确认。

## 隔离承诺

- 代码、工具链、虚拟环境、数据和日志全部位于本目录；
- 不修改根目录下的 `AI数据引擎`、`Agent`、`说明文件` 和 `.runtime`；
- 自动化测试默认只使用 `tests/fixtures` 中的合成数据；
- 真实数据只通过 SQLite `mode=ro` 与 `query_only=ON` 读取；
- 影子 generation、质量旁路和运行报告只写入 `data/shadow/`。

## 快速开始

Rust GNU 工具链在 Windows 上不能可靠处理中文绝对路径，因此先把本目录映射为不覆盖真实文件的 ASCII 盘符。该操作不复制、不移动原项目，也不修改系统 PATH。

```powershell
$workspace = .\scripts\mount-workspace.ps1
Set-Location $workspace
.\scripts\bootstrap-rust.ps1
.\scripts\bootstrap-python.ps1
. .\scripts\env.ps1
cargo test --workspace
.\scripts\acceptance.ps1
```

默认映射为 `R:`；如果该盘符已被其他目录占用，脚本会停止并提示改用 `-Drive Q:`，不会覆盖。`env.ps1` 只修改当前 PowerShell 进程环境，不写系统 PATH；Rust、Cargo、Python 虚拟环境和构建产物仍全部保存在本目录。

## 核心命令

```powershell
cnts build --schema schemas/demo-sales.json --input tests/fixtures/demo-sales.jsonl --root data
cnts inspect --root data
cnts verify --root data
cnts query --root data --plan tests/fixtures/query-total.json
cnts batch-query --root data --plans tests/fixtures/query-batch.json
cnts rollback --root data
```

## 阶段 1 影子迁移

从物理项目目录运行以下脚本。脚本会自动使用 ASCII 映射盘构建工具，但所有文件仍保存在本项目目录：

```powershell
.\scripts\run-oil-daily-shadow.ps1
```

流程为：旧 SQLite 只读分批抽取 -> 精确定点规范化 -> 质量 sidecar -> 候选 CNTS 构建 -> `inspect/verify` -> 214 条批量对账 -> 通过后原子发布。构建成功后会删除可重建的 staging JSONL，保留两代最终 generation 供回滚。

最终实测：859,460 行、182 日、2,003 个拓扑节点；CNTS 为 19,164,045 字节，相对日报表及其索引的 543,395,840 字节缩小 28.35 倍。该结果只证明当前日报影子场景，不代表已满足 5,000 万行生产要求。

## 历史阶段 0：单数据集影子验证边界

本节描述早期格式验证的历史范围，适用于 `tests/fixtures` 和单数据集影子构建命令，不代表当前 Catalog 运行配置档的能力边界：

- 当时已验证：不可变 generation、原子 `CURRENT`、时间主链、共有特征物化下钻、周存聚合、外链倒排、精确明细回退、损坏检测和回滚；
- 单数据集验证只接受 `merge_mode=separate`，多树合并、同比/环比和跨数据集口径当时尚未纳入该测试阶段；
- 当前实现已升级为下文的**多数据集运行配置档**：五个逻辑数据集与全部语义资产由一个不可变 Catalog 绑定；
- 不论历史阶段还是当前 Catalog，CNTS 都不代表已完成 5,000 万行生产压测。查询无命中时仍返回空结果，不把“没有数据”伪装为数值 `0`。

CNTS 的定位、隔离边界、迁移门槛和历史验证结论见 [验收、迁移与演进](../docs/验收、迁移与演进.md)。

## 多数据集运行配置档

当前 CNTS 使用多数据集 Catalog 契约与五个逻辑数据集 schema。Catalog 将四棵事实智慧树固定到各自不可变 generation，并把 `oil_master` 作为独立基座主数据资产；同时绑定基座特征、组织桥、时间规则、AI/经验库、查询契约和拓扑 schema。缺失任一必需数据集或资产时不得进入生产验收。

Catalog 已支持稳定哈希、指定 generation 原生校验、目录越界防护、逐资产校验和不可变原子发布。现有 API、数据集页面、基座特征页面、时间规则页面、智慧树拓扑和统一查询均按请求开始时固定的活动配置档读取；CNTS 活动 Catalog 的导入、注销、重建、生长和语义资产修改入口会被明确阻断，必须通过新候选 Catalog 发布。

五个逻辑数据集由声明驱动构建器统一生成。旧 SQLite 只在候选构建期间以只读方式分批抽取，运行时 CNTS 只读取已发布的不可变 Catalog、事实 generation、基座主数据与配套语义资产。
