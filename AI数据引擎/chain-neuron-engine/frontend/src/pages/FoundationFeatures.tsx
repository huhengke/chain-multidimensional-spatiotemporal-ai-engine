import {
  CheckOutlined,
  CloseOutlined,
  DeleteOutlined,
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  List,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tabs,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useEffect, useMemo, useState } from "react";

import { errorMessage } from "../api/client";
import {
  analyzeFoundationFeatures,
  decideFoundationSuggestion,
  fetchFoundationFeatures,
  listDatasets,
  updateFoundationFeatures,
} from "../api/endpoints";
import RulesPage from "./Rules";
import type {
  DatasetSummary,
  FoundationConfig,
  FoundationField,
  FoundationSuggestion,
} from "../api/types";
import { useSearchParams } from "react-router-dom";

const { Text, Paragraph } = Typography;

const SEMANTIC_OPTIONS = [
  { value: "product_code", label: "产品编码" },
  { value: "product_name", label: "产品名称" },
  { value: "alias", label: "别名/简称" },
  { value: "category", label: "类别" },
  { value: "org_code", label: "组织编码" },
  { value: "org_name", label: "组织名称" },
  { value: "time", label: "时间（建议放主干）" },
  { value: "other", label: "其他" },
];

const semanticLabel = (value?: string) =>
  SEMANTIC_OPTIONS.find((item) => item.value === value)?.label || value || "未设置";

const cloneConfig = (config: FoundationConfig): FoundationConfig =>
  JSON.parse(JSON.stringify(config)) as FoundationConfig;

const blankField = (): FoundationField => ({
  source: "",
  name: "",
  label: "",
  semantic_type: "other",
  layer: "base",
  enabled: true,
  recommendation_source: "manual",
  reason: "人工添加的基座语义字段",
});

function validateConfig(config: FoundationConfig): string | null {
  for (const [dataset, fields] of Object.entries(config.dataset_bindings || {})) {
    const sources = new Set<string>();
    for (const field of fields) {
      const source = String(field.source || "").trim();
      if (!source) return `数据集“${dataset}”存在空字段名，请补充后再保存`;
      if (sources.has(source)) return `数据集“${dataset}”的字段“${source}”重复`;
      sources.add(source);
    }
  }
  return null;
}

export default function FoundationFeaturesPage() {
  const [searchParams] = useSearchParams();
  const [config, setConfig] = useState<FoundationConfig | null>(null);
  const [draft, setDraft] = useState("");
  const [idea, setIdea] = useState("");
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [saving, setSaving] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [activeTab, setActiveTab] = useState(searchParams.get("tab") || "overview");
  const readOnly = Boolean(config?.read_only);

  const applyConfig = (next: FoundationConfig) => {
    setConfig(next);
    setDraft(JSON.stringify(next, null, 2));
  };

  const load = async () => {
    try {
      const [foundation, registry] = await Promise.all([
        fetchFoundationFeatures(),
        listDatasets(),
      ]);
      applyConfig(foundation);
      setDatasets(registry.items);
      const ids = Object.keys(foundation.dataset_bindings || {});
      setSelectedDataset((current) => current || ids[0] || registry.items[0]?.id || "");
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    const tab = searchParams.get("tab");
    if (tab) setActiveTab(tab);
  }, [searchParams]);

  const rows = useMemo(
    () =>
      Object.entries(config?.dataset_bindings || {}).flatMap(([dataset, fields]) =>
        fields.map((field, index) => ({ ...field, dataset, index })),
      ),
    [config],
  );
  const temporalRows = useMemo(
    () =>
      Object.entries(config?.temporal_bindings || {}).flatMap(([dataset, fields]) =>
        fields.map((field) => ({ ...field, dataset })),
      ),
    [config],
  );
  const pending = (config?.pending_suggestions || []).filter(
    (item) => item.status === "pending_review",
  );
  const datasetOptions = useMemo(() => {
    const names = new Set([
      ...Object.keys(config?.dataset_bindings || {}),
      ...datasets.map((dataset) => dataset.id),
    ]);
    return Array.from(names).map((id) => {
      const dataset = datasets.find((item) => item.id === id);
      return {
        value: id,
        label: dataset?.label ? `${dataset.label}（${id}）` : id,
      };
    });
  }, [config, datasets]);
  const selectedFields = config?.dataset_bindings?.[selectedDataset] || [];

  const persist = async (candidate: FoundationConfig) => {
    if (readOnly) {
      message.warning("当前显示的是活动 CNTS Catalog 固定资产；请构建新的候选 Catalog 后再切换");
      return;
    }
    const validation = validateConfig(candidate);
    if (validation) {
      message.warning(validation);
      return;
    }
    setSaving(true);
    try {
      const result = await updateFoundationFeatures(candidate);
      applyConfig(result.config);
      message.success(result.message);
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const updateField = (index: number, patch: Partial<FoundationField>) => {
    if (!config || !selectedDataset) return;
    const next = cloneConfig(config);
    const fields = next.dataset_bindings[selectedDataset] || [];
    fields[index] = { ...fields[index], ...patch };
    next.dataset_bindings[selectedDataset] = fields;
    setConfig(next);
    setDraft(JSON.stringify(next, null, 2));
  };

  const addField = () => {
    if (!config || !selectedDataset) return;
    const next = cloneConfig(config);
    next.dataset_bindings[selectedDataset] = [
      ...(next.dataset_bindings[selectedDataset] || []),
      blankField(),
    ];
    setConfig(next);
    setDraft(JSON.stringify(next, null, 2));
  };

  const removeField = (index: number) => {
    if (!config || !selectedDataset) return;
    const next = cloneConfig(config);
    next.dataset_bindings[selectedDataset] = (
      next.dataset_bindings[selectedDataset] || []
    ).filter((_, itemIndex) => itemIndex !== index);
    setConfig(next);
    setDraft(JSON.stringify(next, null, 2));
  };

  const saveAdvanced = async () => {
    try {
      await persist(JSON.parse(draft) as FoundationConfig);
    } catch (e) {
      message.error(e instanceof SyntaxError ? "JSON 配置格式不正确" : errorMessage(e));
    }
  };

  const analyze = async () => {
    if (readOnly) {
      message.warning("活动 CNTS Catalog 不允许原地新增建议；请在候选 Catalog 中修改");
      return;
    }
    if (!idea.trim()) {
      message.warning("请先写下希望基座解决的语义问题");
      return;
    }
    setAnalyzing(true);
    try {
      const result = await analyzeFoundationFeatures({
        idea,
        // 列表接口已经提供字段、时间和结构摘要；AI 建议不需要读取完整拓扑详情。
        // 避免为了生成基座建议而触发全量节点统计，导致页面等待超时。
        datasets: datasets.map((dataset) => ({
          id: dataset.id,
          label: dataset.label,
          kind: dataset.kind,
          columns: (() => {
            const structureFields = dataset.structure?.feature_layers || [];
            const fallbackFields =
              dataset.columns ||
              (dataset.fact_columns as unknown[] | undefined)?.map((source) => ({
                source,
                name: source,
                label: source,
              })) || [];
            const timeField = dataset.time?.source
              ? [
                  {
                    source: dataset.time.source,
                    name: dataset.time.source,
                    label: "时间",
                    role: "time",
                    semantic_type: "time",
                    feature_layer: "trunk",
                    time_format: dataset.time.format,
                    canonical_time: true,
                  },
                ]
              : [];
            const all = [...structureFields, ...fallbackFields, ...timeField] as Array<
              Record<string, unknown>
            >;
            const seen = new Set<string>();
            return all.filter((field) => {
              const source = String(field.source || field.name || "");
              if (!source || seen.has(source)) return false;
              seen.add(source);
              return true;
            });
          })(),
        })),
      });
      applyConfig(result.config);
      message.success(`${result.message}（${result.backend}）`);
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const decide = async (item: FoundationSuggestion, approve: boolean) => {
    if (readOnly) {
      message.warning("活动 CNTS Catalog 为只读，不能原地确认或拒绝建议");
      return;
    }
    try {
      const result = await decideFoundationSuggestion(item.id, approve);
      applyConfig(result.config);
      message.success(result.message);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: "100%", minWidth: 0 }}>
      <Alert
        type="info"
        showIcon
        message="基座特征管理是问题分解规范器，不是时间序列存储层。"
        description="它把用户说法翻译、过滤、纠偏成智慧树可以执行的标准参数。原始日期/年月字段进入中通主干；时间规则在“时间规则生成器”中维护并由基座编译；指标进入分枝。经验库与 AI 只产生建议，人工确认后才生效。"
      />

      {readOnly && (
        <Alert
          type="warning"
          showIcon
          message="当前展示活动 CNTS Catalog 的固定基座资产"
          description={`Catalog：${config?.catalog_id || "-"}；此页只读。修改基座或时间规则后需构建新的候选 Catalog，完成验收再原子切换。`}
        />
      )}

      <Tabs
        type="card"
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          { key: "overview", label: "基座总览" },
          { key: "suggestions", label: `AI/经验建议${pending.length ? `（${pending.length}）` : ""}` },
          { key: "maintenance", label: "字段维护" },
          { key: "time-rules", label: "时间规则生成器" },
          { key: "advanced", label: "高级配置" },
        ]}
      />

      {activeTab === "overview" && (
        <>

      <Card
        title="基座语义总览"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            重新加载
          </Button>
        }
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Collapse
            items={[
              {
                key: "semantic",
                label: "查看基座原则与来源（高级信息）",
                children: (
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(config?.global_semantics || {}, null, 2)}
                  </pre>
                ),
              },
            ]}
          />
          <Typography.Text type="secondary">
            当前版本 v{config?.version ?? "-"} · 更新时间 {config?.updated_at || "-"} ·
            数据集绑定 {Object.keys(config?.dataset_bindings || {}).length} 个
          </Typography.Text>
        </Space>
      </Card>

      <Card title="经验库推荐索引">
        <Typography.Text type="secondary">
          以下条目来自不可删除的 ai_field_recommendations.yaml 版本化资产；它们是建议依据，不会绕过人工确认直接修改基座。
        </Typography.Text>
        <Table
          style={{ marginTop: 12 }}
          size="small"
          rowKey={(row) => String(row.id || "experience")}
          dataSource={config?.experience_recommendations || []}
          pagination={{ pageSize: 8 }}
          columns={[
            { title: "经验条目", dataIndex: "id" },
            { title: "角色", dataIndex: "role" },
            { title: "语义", dataIndex: "semantic_type" },
            {
              title: "基座候选",
              dataIndex: "base_candidate",
              render: (value: boolean) => (
                <Tag color={value ? "purple" : "default"}>{value ? "是" : "否"}</Tag>
              ),
            },
            { title: "依据", dataIndex: "rationale", ellipsis: true },
          ]}
        />
      </Card>

      <Card title="通用时间语义与主干时间入口">
        <Alert
          type="info"
          showIcon
          message="基座先把自然语言时间翻译成标准范围；原始日期/年月字段进入中通主干。"
          description={
            <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(
                {
                  common_time_semantics: config?.global_semantics?.common_time_semantics || {},
                  temporal_bindings: config?.temporal_bindings || {},
                },
                null,
                2,
              )}
            </pre>
          }
        />
        <Table
          size="small"
          style={{ marginTop: 12 }}
          rowKey={(row) => row.dataset + "-" + row.source}
          dataSource={temporalRows}
          pagination={{ pageSize: 12 }}
          columns={[
            { title: "数据集", dataIndex: "dataset" },
            { title: "时间字段", dataIndex: "source" },
            { title: "显示名", dataIndex: "label" },
            { title: "归属", render: () => <Tag color="cyan">中通主干</Tag> },
            { title: "来源", dataIndex: "recommendation_source" },
            { title: "依据", dataIndex: "reason", ellipsis: true },
          ]}
          locale={{ emptyText: "暂无已声明的时间入口" }}
        />
      </Card>

        </>
      )}

      {activeTab === "suggestions" && (
        <>
      <Card
        title="AI/经验融合建议"
        extra={
          <Button type="primary" loading={analyzing} disabled={readOnly} onClick={() => void analyze()}>
            分析并生成待确认建议
          </Button>
        }
      >
        <Input.TextArea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder="例如：用户说‘类别甲’时，希望基座能翻译为产品类别对应的多个编码；‘示例区域甲’要按每个事实数据集的组织字段分别解析。"
          autoSize={{ minRows: 2, maxRows: 5 }}
        />
        <List
          style={{ marginTop: 12 }}
          bordered
          dataSource={pending}
          locale={{ emptyText: "暂无待确认建议" }}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button
                  key="approve"
                  icon={<CheckOutlined />}
                  type="link"
                  disabled={readOnly}
                  onClick={() => void decide(item, true)}
                >
                  确认加入
                </Button>,
                <Button
                  key="reject"
                  icon={<CloseOutlined />}
                  danger
                  type="link"
                  disabled={readOnly}
                  onClick={() => void decide(item, false)}
                >
                  拒绝
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Tag color="blue">{item.dataset}</Tag>
                    <Tag>{item.source}</Tag>
                  </Space>
                }
                description={
                  <>
                    <Paragraph style={{ marginBottom: 4 }}>{item.rationale}</Paragraph>
                    <Text type="secondary">
                      候选字段：{item.fields.map((field) => field.label || field.source).join("、")}
                    </Text>
                  </>
                }
              />
            </List.Item>
          )}
        />
      </Card>
        </>
      )}

      {activeTab === "maintenance" && (
        <>
      <Card
        title="基座字段表单维护"
        extra={
          <Space>
            <Select
              showSearch
              value={selectedDataset || undefined}
              onChange={setSelectedDataset}
              options={datasetOptions}
              placeholder="选择数据集"
              style={{ minWidth: 260 }}
            />
            <Button icon={<PlusOutlined />} onClick={addField} disabled={!selectedDataset || readOnly}>
              添加字段
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              disabled={!config || readOnly}
              onClick={() => config && void persist(config)}
            >
              保存表单配置
            </Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          message="每一行就是一个基座语义字段"
          description="字段名填写数据集中的真实列名；语义类型用于告诉系统如何把用户说法翻译成数据条件。时间字段请维护在上面的“主干时间入口”，不要重复添加到基座。"
        />
        <Table
          style={{ marginTop: 12 }}
          size="small"
          scroll={{ x: 1160 }}
          rowKey={(_, index) => `${selectedDataset}-${index}`}
          dataSource={selectedFields.map((field, index) => ({ ...field, index }))}
          pagination={{ pageSize: 12, showSizeChanger: true }}
          locale={{
            emptyText: selectedDataset ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="该数据集暂无基座字段，可点击“添加字段”开始维护"
              />
            ) : (
              "请先选择数据集"
            ),
          }}
          columns={[
            {
              title: "字段名（真实列名）",
              dataIndex: "source",
              width: 220,
              render: (value: string, row: FoundationField & { index: number }) => (
                <Input
                  size="small"
                  value={value}
                  placeholder="例如 MaterialCode"
                  disabled={readOnly}
                  onChange={(e) => updateField(row.index, { source: e.target.value })}
                />
              ),
            },
            {
              title: "内部名称",
              dataIndex: "name",
              width: 180,
              render: (value: string, row: FoundationField & { index: number }) => (
                <Input
                  size="small"
                  value={value}
                  placeholder="系统内部名称"
                  disabled={readOnly}
                  onChange={(e) => updateField(row.index, { name: e.target.value })}
                />
              ),
            },
            {
              title: "用户看到的名称",
              dataIndex: "label",
              width: 180,
              render: (value: string, row: FoundationField & { index: number }) => (
                <Input
                  size="small"
                  value={value}
                  placeholder="例如 产品编码"
                  disabled={readOnly}
                  onChange={(e) => updateField(row.index, { label: e.target.value })}
                />
              ),
            },
            {
              title: "语义类型",
              dataIndex: "semantic_type",
              width: 180,
              render: (value: string, row: FoundationField & { index: number }) => (
                <Select
                  size="small"
                  value={value || "other"}
                  options={SEMANTIC_OPTIONS}
                  disabled={readOnly}
                  style={{ width: "100%" }}
                  onChange={(next) => updateField(row.index, { semantic_type: next })}
                />
              ),
            },
            {
              title: "说明",
              dataIndex: "reason",
              width: 300,
              render: (value: string, row: FoundationField & { index: number }) => (
                <Input
                  size="small"
                  value={value}
                  placeholder="这个字段为什么用于基座过滤/翻译"
                  disabled={readOnly}
                  onChange={(e) => updateField(row.index, { reason: e.target.value })}
                />
              ),
            },
            {
              title: "状态",
              dataIndex: "enabled",
              width: 90,
              render: (value: boolean, row: FoundationField & { index: number }) => (
                <Switch
                  size="small"
                  checked={value}
                  checkedChildren="启用"
                  unCheckedChildren="停用"
                  disabled={readOnly}
                  onChange={(checked) => updateField(row.index, { enabled: checked })}
                />
              ),
            },
            {
              title: "来源",
              dataIndex: "recommendation_source",
              width: 100,
              render: (value: string) => <Tag>{value === "manual" ? "人工" : value}</Tag>,
            },
            {
              title: "操作",
              width: 80,
              render: (_: unknown, row: FoundationField & { index: number }) => (
                <Popconfirm
                  title="删除这个基座字段？"
                  description="删除后需要点击保存配置才会写入系统。"
                  onConfirm={() => removeField(row.index)}
                  okText="删除"
                  cancelText="取消"
                >
                  <Button danger type="link" icon={<DeleteOutlined />} disabled={readOnly} />
                </Popconfirm>
              ),
            },
          ]}
        />
        {selectedFields.length > 0 ? (
          <Text type="secondary" style={{ display: "block", marginTop: 10 }}>
            当前数据集共 {selectedFields.length} 个基座字段；时间字段不在此处维护，统一查看上面的中通主干入口。
          </Text>
        ) : null}
      </Card>

      <Card title="已确认的基座字段总览" extra={<Text type="secondary">共 {rows.length} 个字段</Text>}>
        <Table
          size="small"
          rowKey={(row) => row.dataset + "-" + row.source}
          dataSource={rows}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: "数据集", dataIndex: "dataset" },
            { title: "字段", dataIndex: "source" },
            { title: "显示名", dataIndex: "label" },
            {
              title: "语义",
              dataIndex: "semantic_type",
              render: (value: string) => semanticLabel(value),
            },
            {
              title: "来源",
              dataIndex: "recommendation_source",
              render: (value: string) => (
                <Tag color={value === "manual" ? "blue" : value === "ai" ? "green" : "gold"}>
                  {value === "manual" ? "人工" : value}
                </Tag>
              ),
            },
            {
              title: "状态",
              dataIndex: "enabled",
              render: (value: boolean) => (
                <Tag color={value ? "green" : "default"}>{value ? "已启用" : "已停用"}</Tag>
              ),
            },
          ]}
        />
      </Card>

        </>
      )}

      {activeTab === "time-rules" && <RulesPage embedded />}

      {activeTab === "advanced" && (
        <>
      <Card
        title="高级人工维护（版本化 JSON，可选）"
        extra={
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            disabled={readOnly}
            onClick={() => void saveAdvanced()}
          >
            校验并保存 JSON
          </Button>
        }
      >
        <Collapse
          items={[
            {
              key: "advanced-json",
              label: "展开 JSON 编辑器（熟悉配置格式时使用）",
              children: (
                <Input.TextArea
                  value={draft}
                  disabled={readOnly}
                  onChange={(e) => setDraft(e.target.value)}
                  autoSize={{ minRows: 18, maxRows: 36 }}
                  style={{ fontFamily: "monospace", fontSize: 12 }}
                />
              ),
            },
          ]}
        />
        <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
          表单保存和 JSON 保存都会生成上一版本快照；不会修改不可删除的 ai_field_recommendations.yaml 经验库。
        </Typography.Text>
      </Card>
        </>
      )}
    </Space>
  );
}
