import {
  ApartmentOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Divider,
  List,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { errorMessage } from "../api/client";
import {
  deleteDataset,
  getDataset,
  importDataset,
  listDatasetRows,
  listDatasets,
  rebuildDataset,
  reloadDatasets,
  restoreDataset,
} from "../api/endpoints";
import type {
  AggregateScope,
  DatasetRowsResponse,
  DatasetStructure,
  DatasetSummary,
  FeatureLayer,
} from "../api/types";
import ImportProgress from "../components/ImportProgress";
import { useAuth } from "../auth/AuthContext";
import { useImportTask } from "../hooks/useImportTask";

const KIND_META: Record<string, { color: string; label: string }> = {
  generic: { color: "green", label: "通用事实" },
  dimension: { color: "purple", label: "维表/主数据" },
};

const LAYER_META: Record<FeatureLayer, { color: string; label: string }> = {
  base: { color: "purple", label: "基座" },
  trunk: { color: "cyan", label: "中通主干" },
  branch: { color: "orange", label: "中通分枝" },
  none: { color: "default", label: "忽略" },
};

const USE_LABELS: Record<string, string> = {
  filter: "过滤",
  group_by: "分组",
  aggregate: "聚合",
  sort: "排序",
  join: "关联",
  compare: "对比",
};

const SCOPE_OPTIONS: { value: AggregateScope; label: string }[] = [
  { value: "all", label: "全部节点" },
  { value: "base", label: "基座相关节点" },
  { value: "trunk", label: "中通主干节点" },
  { value: "branch", label: "中通分枝节点" },
];

function LayerTags({ layers }: { layers: FeatureLayer[] }) {
  return (
    <Space size={4} wrap>
      {layers.length ? layers.map((layer) => (
        <Tag key={layer} color={LAYER_META[layer]?.color}>
          {LAYER_META[layer]?.label || layer}
        </Tag>
      )) : <Tag>未登记</Tag>}
    </Space>
  );
}

function StructureDrawer({
  dataset,
  open,
  rebuilding,
  onClose,
  onRebuild,
}: {
  dataset: DatasetSummary | null;
  open: boolean;
  rebuilding: boolean;
  onClose: () => void;
  onRebuild: (scope: AggregateScope) => void;
}) {
  const [scope, setScope] = useState<AggregateScope>("all");
  const structure = dataset?.structure as DatasetStructure | undefined;
  const catalogSource = dataset?.source?.kind === "cnts"
    || dataset?.source_origin === "catalog"
    || dataset?.origin === "catalog";

  useEffect(() => {
    setScope(structure?.aggregation.default_scope || "all");
  }, [dataset?.id, structure?.aggregation.default_scope]);

  return (
    <Drawer
      width={980}
      open={open}
      title={dataset ? `空间结构 · ${dataset.label}（${dataset.id}）` : "空间结构"}
      onClose={onClose}
    >
      {!dataset || !structure ? (
        <Alert type="warning" showIcon message="当前数据集还没有可展示的结构摘要" />
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Alert
            type={structure.feature_layer_source !== "fallback" ? "info" : "warning"}
            showIcon
            message={
              structure.feature_layer_source === "catalog"
                ? "以下结构来自当前 CNTS Catalog 固定 Generation"
                : structure.feature_layer_source === "declared"
                  ? "以下结构来自已保存的字段确认声明"
                  : "以下结构由现有时间/层级/指标声明回退生成，旧声明尚未记录基座/主干/分枝归属"
            }
            description={catalogSource
              ? "当前结构只读；修改数据、阈值或字段后，应构建并验收新的候选 Catalog，再原子切换。"
              : "同一字段可以同时属于多个结构层；标签会完整显示跨层复用关系。"}
          />

          <Descriptions bordered size="small" column={3}>
            <Descriptions.Item label="基座特征">
              {structure.layer_counts.base} 个
            </Descriptions.Item>
            <Descriptions.Item label="中通主干特征">
              {structure.layer_counts.trunk} 个
            </Descriptions.Item>
            <Descriptions.Item label="中通分枝特征">
              {structure.layer_counts.branch} 个
            </Descriptions.Item>
            <Descriptions.Item label="DNA 叠加层数">
              {structure.dna.layer_count} 层
            </Descriptions.Item>
            <Descriptions.Item label="DNA 长度">
              {structure.dna.length} 个字段
            </Descriptions.Item>
            <Descriptions.Item label="语义路径层数">
              {structure.spiral.length} 层
            </Descriptions.Item>
            <Descriptions.Item label="物理时间圈层数">
              {structure.physical_topology?.temporal_layers ?? structure.physical_topology?.helix_layers ?? 0} 圈
            </Descriptions.Item>
          </Descriptions>

          <Card size="small" title="字段空间归属">
            <Table
              size="small"
              rowKey="source"
              pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
              scroll={{ x: "max-content" }}
              dataSource={structure.feature_layers}
              columns={[
                { title: "字段", dataIndex: "source", fixed: "left" as const },
                { title: "显示名", dataIndex: "label" },
                {
                  title: "归属层",
                  dataIndex: "layers",
                  render: (layers: FeatureLayer[]) => <LayerTags layers={layers} />,
                },
                { title: "角色", dataIndex: "role" },
                {
                  title: "查询用途",
                  dataIndex: "query_uses",
                  render: (uses: string[]) => (
                    <Space size={4} wrap>
                      {(uses || []).map((use) => <Tag key={use}>{USE_LABELS[use] || use}</Tag>)}
                    </Space>
                  ),
                },
                { title: "语义", dataIndex: "semantic_type" },
              ]}
            />
          </Card>

          <Card size="small" title={`DNA 双螺旋叠加序列（${structure.dna.length}）`}>
            <Space size={4} wrap>
              {structure.dna.keys.map((key, index) => (
                <Tag key={`${key}-${index}`} color="blue">
                  {index + 1}. {key}
                </Tag>
              ))}
            </Space>
          </Card>

          <Card size="small" title={`中通语义路径层级（${structure.spiral.length} 层）`}>
            <List
              size="small"
              bordered
              dataSource={structure.spiral.levels}
              locale={{ emptyText: "维表/主数据不参与中通语义路径" }}
              renderItem={(level) => (
                <List.Item>
                  <Space wrap>
                    <Tag color="geekblue">第 {level.index} 层</Tag>
                    <Typography.Text strong>{level.label}</Typography.Text>
                    <Typography.Text type="secondary">{level.name} ← {level.source}</Typography.Text>
                    <LayerTags layers={level.layers} />
                  </Space>
                </List.Item>
              )}
            />
          </Card>

          <Card size="small" title="动态生长">
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Space wrap>
                <Tag color={structure.growth.enabled ? "green" : "default"}>
                  {structure.growth.enabled ? "涉及动态生长" : "不参与动态生长"}
                </Tag>
                <Tag>{structure.growth.configured ? "已显式配置" : "通用事实默认能力"}</Tag>
                {structure.growth.enabled && <Tag>阈值 {structure.growth.threshold}</Tag>}
                <Typography.Text type="secondary">
                  节点 {structure.growth.node_count} · 已生长 {structure.growth.grown_node_count} · 待生长 {structure.growth.candidate_node_count}
                </Typography.Text>
              </Space>
              {structure.growth.grown_paths.length > 0 ? (
                <List
                  size="small"
                  dataSource={structure.growth.grown_paths}
                  renderItem={(path) => (
                    <List.Item>
                      <Space wrap>
                        <Tag color="green">第 {path.level} 层 · {path.level_label}</Tag>
                        <Typography.Text>{path.node_value}</Typography.Text>
                        <Typography.Text type="secondary">路径：{path.path}</Typography.Text>
                        <Typography.Text type="secondary">记录 {path.record_count}</Typography.Text>
                      </Space>
                    </List.Item>
                  )}
                />
              ) : (
                <Typography.Text type="secondary">当前没有已生长节点；待生长节点达到阈值后会在这里标注具体层级和路径。</Typography.Text>
              )}
            </Space>
          </Card>

          {dataset.kind !== "dimension" && !catalogSource && (
            <Card size="small" title="按结构范围重算聚合">
              <Space wrap>
                <Select
                  value={scope}
                  style={{ width: 220 }}
                  options={SCOPE_OPTIONS}
                  onChange={(value) => setScope(value as AggregateScope)}
                />
                <Button
                  type="primary"
                  icon={<ApartmentOutlined />}
                  loading={rebuilding}
                  onClick={() => onRebuild(scope)}
                >
                  执行范围重算
                </Button>
              </Space>
              <Divider style={{ margin: "12px 0" }} />
              <Typography.Text type="secondary">
                只计算所选结构范围对应的节点；“全部节点”才会执行整棵螺旋树重算。
              </Typography.Text>
            </Card>
          )}
        </Space>
      )}
    </Drawer>
  );
}

export default function DatasetsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [items, setItems] = useState<DatasetSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<DatasetRowsResponse | null>(null);
  const [rowsTarget, setRowsTarget] = useState<string>("");
  const [rowsPage, setRowsPage] = useState(1);
  const [structureTarget, setStructureTarget] = useState<string>("");
  const [structureDetail, setStructureDetail] = useState<DatasetSummary | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const { task, percent, running, watch } = useImportTask();
  const catalogMode = items.length > 0 && items.every((item) => (
    item.source?.kind === "cnts"
    || item.source_origin === "catalog"
    || item.origin === "catalog"
  ));

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listDatasets();
      setItems(r.items);
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openRows = async (dataset: string, page = 1) => {
    try {
      const r = await listDatasetRows(dataset, page, 20);
      setRows(r);
      setRowsTarget(dataset);
      setRowsPage(page);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  const onImport = async (dataset: string, file: File) => {
    try {
      const { task_uuid } = await importDataset(dataset, "replace", { file });
      message.success("导入任务已触发");
      await watch(task_uuid);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  const onRegisteredSourceImport = async (dataset: string) => {
    try {
      const { task_uuid } = await importDataset(dataset, "replace");
      message.success("已按注册来源触发导入任务");
      await watch(task_uuid);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  const onRebuild = async (dataset: string, scope: AggregateScope) => {
    setRebuilding(true);
    try {
      const r = await rebuildDataset(dataset, scope);
      message.success(r.message || "重算完成");
      await load();
      if (structureTarget === dataset) {
        setStructureDetail(await getDataset(dataset));
      }
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setRebuilding(false);
    }
  };

  const openStructure = async (dataset: string) => {
    setStructureTarget(dataset);
    try {
      setStructureDetail(await getDataset(dataset));
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  const onDelete = async (dataset: string) => {
    try {
      await deleteDataset(dataset);
      message.success(`数据集 ${dataset} 已注销，物理表与螺旋节点已清理`);
      void load();
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  const onRestore = async (dataset: string) => {
    try {
      const r = await restoreDataset(dataset);
      message.success(r.message || `数据集 ${dataset} 已恢复`);
      void load();
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  const columns = [
    {
      title: "数据集",
      dataIndex: "label",
      render: (v: string, r: DatasetSummary) => (
        <Space direction="vertical" size={0}>
          <span>{v}</span>
          <span style={{ fontSize: 12, color: "#888" }}>{r.id}</span>
        </Space>
      ),
    },
    {
      title: "类型",
      dataIndex: "kind",
      width: 150,
      render: (k: string) => (
        <Tag color={KIND_META[k]?.color}>{KIND_META[k]?.label || k}</Tag>
      ),
    },
    {
      title: "来源",
      width: 180,
      render: (_: unknown, r: DatasetSummary) => (
        <Space size={4} wrap>
          {(r.source_origin || r.origin) === "catalog" ? (
            <Tag color="geekblue">CNTS Catalog</Tag>
          ) : (r.source_origin || r.origin) === "interface" ? (
            <Tag color="orange">界面注册</Tag>
          ) : (
            <Tag color="gold">基座数据</Tag>
          )}
          {r.source?.kind === "database" && <Tag color="cyan">数据库表</Tag>}
          {(r.source?.kind === "excel" || r.source_file) && <Tag color="green">Excel</Tag>}
          {r.base?.enabled && <Tag color="blue">已启用基座能力</Tag>}
          {r.disabled && <Tag color="red">已注销</Tag>}
        </Space>
      ),
    },
    {
      title: "结构",
      width: 420,
      render: (_: unknown, r: DatasetSummary) => {
        const structure = r.structure;
        if (!structure) return <Typography.Text type="secondary">暂无结构摘要</Typography.Text>;
        return (
          <Space direction="vertical" size={2}>
            <Space size={4} wrap>
              <Tag color="purple">基座 {structure.layer_counts.base}</Tag>
              <Tag color="cyan">主干 {structure.layer_counts.trunk}</Tag>
              <Tag color="orange">分枝 {structure.layer_counts.branch}</Tag>
                <Tag color="blue">DNA {structure.dna.layer_count} 层 / {structure.dna.length}</Tag>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              语义层 {structure.spiral.length} 层 · {structure.growth.enabled
                ? structure.growth.stats_available
                  ? `已生长 ${structure.growth.grown_node_count}/${structure.growth.node_count}`
                  : "涉及动态生长（点击查看详情）"
                : "不参与生长"}
              {structure.feature_layer_source === "fallback" ? " · 旧声明回退展示" : ""}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "操作",
      width: 360,
      render: (_: unknown, r: DatasetSummary) => {
        if (r.disabled) {
          return (
            <Space size={4} wrap>
              <Tag color="red">已注销</Tag>
              {r.can_restore && isAdmin && (
                <Button size="small" onClick={() => void onRestore(r.id)}>
                  恢复基座
                </Button>
              )}
            </Space>
          );
        }
        const databaseSource = r.source?.kind === "database";
        const excelSource = r.source?.kind === "excel" || Boolean(r.source_file);
        const catalogSource = r.source?.kind === "cnts"
          || r.source_origin === "catalog"
          || r.origin === "catalog";
        const canImport = databaseSource || excelSource || r.editable;
        return (
          <Space size={4} wrap>
            {!catalogSource && (
              <Button size="small" onClick={() => void openRows(r.id)}>
                浏览数据
              </Button>
            )}
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => void openStructure(r.id)}
            >
              查看结构
            </Button>
            {!catalogSource && r.editable && isAdmin && (
              <Button
                size="small"
                icon={<EditOutlined />}
                onClick={() => navigate(`/datasets/new?edit=${r.id}`)}
              >
                编辑声明
              </Button>
            )}
            {!catalogSource && canImport && (databaseSource ? (
              <Button size="small" onClick={() => void onRegisteredSourceImport(r.id)}>
                导入注册表
              </Button>
            ) : (
              <label
                style={{
                  display: "inline-block",
                  padding: "0 7px",
                  fontSize: 14,
                  lineHeight: "24px",
                  border: "1px solid #d9d9d9",
                  borderRadius: 6,
                  cursor: "pointer",
                  background: "#fff",
                }}
              >
                导入 Excel
                <input
                  type="file"
                  accept=".xlsx"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void onImport(r.id, f);
                    e.target.value = "";
                  }}
                />
              </label>
            ))}
            {!catalogSource && r.can_unregister !== false && (
              <Popconfirm
                title={`注销 ${r.id}？`}
                description={
                  (r.source_origin || r.origin) === "base"
                    ? "将清理当前物理数据和螺旋节点；基座配置保留，之后可以恢复"
                    : "将同时删除物理表与螺旋节点数据，不可恢复"
                }
                onConfirm={() => void onDelete(r.id)}
              >
                <Button size="small" danger>
                  注销
                </Button>
              </Popconfirm>
            )}
            {catalogSource && <Tag color="blue">固定 Generation · 只读</Tag>}
          </Space>
        );
      },
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message={catalogMode
          ? "当前页面展示活动 CNTS Catalog 的完整数据集集合与固定 Generation。"
          : "数据集管理是数据进入引擎的统一入口：注册 Excel 或数据库表 → AI/规则分析字段 → 确认角色 → 保存 → 导入。想改层级链、换日粒度源文件，用“编辑声明”。"}
        description={catalogMode
          ? "活动 Catalog 不允许原地导入、注销或重算；变更需要生成新的候选 Catalog，完成全量验收后再到系统参数中原子切换。"
          : "数据集类型仅有“通用事实”和“维表/主数据”；数据来源仅有“基座数据”和“界面注册”。通用事实支持浏览、重算聚合、导入 Excel/注册来源和注销；维表支持浏览、导入和注销。基座注销后从主列表移除，需要在一次性架构升级验收页面恢复。"}
      />

      <Card
        title="数据集列表"
        extra={
          <Space>
            {!catalogMode && (
              <Button icon={<ReloadOutlined />} onClick={() => void reloadDatasets().then(() => load())}>
                热加载注册表
              </Button>
            )}
            {!catalogMode && (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={!isAdmin}
                onClick={() => navigate("/datasets/new")}
              >
                注册新数据集
              </Button>
            )}
            {isAdmin && !catalogMode && (
              <Button onClick={() => navigate("/reinitialize")}>
                一次性架构升级验收
              </Button>
            )}
          </Space>
        }
      >
        <Table
          rowKey="id"
          loading={loading}
          dataSource={items}
          columns={columns}
          pagination={false}
        />
        {!isAdmin && !catalogMode && (
          <div style={{ marginTop: 8, color: "#888", fontSize: 12 }}>
            当前角色非管理员：注册与注销操作需要管理员权限。
          </div>
        )}
      </Card>

      {task && <ImportProgress task={task} percent={percent} />}

      <StructureDrawer
        dataset={structureDetail || items.find((item) => item.id === structureTarget) || null}
        open={Boolean(structureTarget)}
        rebuilding={rebuilding}
        onClose={() => {
          setStructureTarget("");
          setStructureDetail(null);
        }}
        onRebuild={(scope) => {
          if (structureTarget) void onRebuild(structureTarget, scope);
        }}
      />

      <Drawer
        width={900}
        open={!!rows}
        title={`数据浏览 · ${rowsTarget}（共 ${rows?.total ?? 0} 行${
          running ? "，导入进行中" : ""
        }）`}
        onClose={() => setRows(null)}
      >
        <Table
          size="small"
          rowKey={(_r, i) => String(i)}
          dataSource={rows?.items || []}
          scroll={{ x: "max-content" }}
          pagination={{
            current: rowsPage,
            pageSize: 20,
            total: rows?.total || 0,
            onChange: (p) => void openRows(rowsTarget, p),
          }}
          columns={(rows?.columns || []).map((c) => ({
            title: c.label === c.name ? c.name : `${c.label}(${c.name})`,
            dataIndex: c.name,
            ellipsis: true,
          }))}
        />
      </Drawer>
    </Space>
  );
}
