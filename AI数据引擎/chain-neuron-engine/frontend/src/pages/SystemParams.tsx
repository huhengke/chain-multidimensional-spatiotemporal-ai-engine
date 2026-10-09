import { ApartmentOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  InputNumber,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import { errorMessage } from "../api/client";
import { fetchSystemParams, listDatasets, rebuildDataset, updateSystemParams } from "../api/endpoints";
import type { SystemParamItem } from "../api/types";
import { useAuth } from "../auth/AuthContext";

const SOURCE_LABEL: Record<string, { text: string; color: string }> = {
  interface: { text: "界面配置", color: "green" },
  env: { text: "环境变量", color: "blue" },
  default: { text: "默认值", color: "default" },
};

export default function SystemParamsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [items, setItems] = useState<SystemParamItem[]>([]);
  const [drafts, setDrafts] = useState<Record<string, number | null>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildStatus, setRebuildStatus] = useState("");
  const [needsRebuild, setNeedsRebuild] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [runtimeLabel, setRuntimeLabel] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetchSystemParams();
      setItems(res.items);
      setDrafts(Object.fromEntries(res.items.map((p) => [p.key, p.value])));
      setReadOnly(Boolean(res.read_only));
      setRuntimeLabel(
        [res.profile_id, res.catalog_id].filter(Boolean).join(" / "),
      );
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const dirty = items.some((p) => drafts[p.key] !== p.value);

  const save = async () => {
    if (readOnly) {
      message.warning("活动 CNTS Catalog 不可直接修改；请构建并验收新的候选配置档");
      return;
    }
    const changed: Record<string, number> = {};
    for (const p of items) {
      const v = drafts[p.key];
      if (v != null && v !== p.value) changed[p.key] = v;
    }
    if (!Object.keys(changed).length) {
      message.info("没有需要保存的修改");
      return;
    }
    setSaving(true);
    try {
      const res = await updateSystemParams(changed);
      setItems(res.items);
      setDrafts(Object.fromEntries(res.items.map((p) => [p.key, p.value])));
      if (Object.prototype.hasOwnProperty.call(changed, "growth_threshold")) {
        setNeedsRebuild(true);
      }
      message.success(res.message || "系统参数已保存并热加载");
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const updateEngineTree = async () => {
    if (readOnly) {
      message.warning("活动 CNTS Generation 不可重建；请按新阈值构建候选 Catalog");
      return;
    }
    setRebuilding(true);
    setRebuildStatus("正在读取启用的数据集…");
    try {
      const registry = await listDatasets();
      const datasets = (registry.items || []).filter((dataset) => !dataset.disabled && dataset.kind !== "dimension");
      if (!datasets.length) {
        message.info("当前没有需要更新智慧树的通用事实数据集");
        return;
      }
      for (const [index, dataset] of datasets.entries()) {
        setRebuildStatus(`正在更新智慧树 ${index + 1}/${datasets.length}：${dataset.label}`);
        await rebuildDataset(dataset.id, "all");
      }
      setNeedsRebuild(false);
      message.success(`已按当前生长阈值更新 ${datasets.length} 个 AI 数据引擎智慧树`);
    } catch (e) {
      message.error(errorMessage(e, "重算过程中断"));
    } finally {
      setRebuilding(false);
      setRebuildStatus("");
    }
  };

  const columns: ColumnsType<SystemParamItem> = [
    {
      title: "参数",
      dataIndex: "label",
      render: (_, p) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{p.label}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {p.description}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            键：{p.key}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "当前值",
      dataIndex: "value",
      width: 180,
      render: (_, p) => (
        <InputNumber
          value={drafts[p.key] ?? p.value}
          min={p.min}
          max={p.max}
          step={1}
          disabled={!isAdmin || readOnly}
          style={{ width: 150 }}
          onChange={(v) => setDrafts((d) => ({ ...d, [p.key]: v }))}
        />
      ),
    },
    {
      title: "允许范围",
      key: "range",
      width: 140,
      render: (_, p) => `${p.min} ~ ${p.max}`,
    },
    {
      title: "默认值",
      dataIndex: "default",
      width: 100,
    },
    {
      title: "来源",
      dataIndex: "source",
      width: 110,
      render: (s: string) => {
        const meta = SOURCE_LABEL[s] || { text: s, color: "default" };
        return <Tag color={meta.color}>{meta.text}</Tag>;
      },
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type={readOnly ? "warning" : "info"}
        showIcon
        message={readOnly ? "当前 CNTS 运行配置只读" : "系统参数保存后立即热加载，无需重启引擎"}
        description={readOnly
          ? `活动配置 ${runtimeLabel || "CNTS Catalog"} 已固定生长参数与拓扑。调整阈值需要构建新的候选 Catalog，完成验收后再原子切换。`
          : "生长阈值控制智慧树“每层多少条数据才继续向外生长”：保存参数只修改运行参数，不会自动改写已有树。请点击“更新 AI 数据引擎-智慧树”，按新阈值重建事实聚合、主干/分枝节点、DNA 拓扑和物化结果，不会重新导入事实明细。"}
      />
      {needsRebuild && (
        <Alert
          type="warning"
          showIcon
          message="生长阈值已改变，已有智慧树尚未更新"
          description="请点击“更新 AI 数据引擎-智慧树”，完成后再刷新 Agent 工作台的智慧树。"
          action={<Button size="small" type="primary" icon={<ApartmentOutlined />} onClick={() => void updateEngineTree()}>更新智慧树</Button>}
        />
      )}
      {!isAdmin && (
        <Alert type="warning" showIcon message="修改系统参数需要管理员权限（当前只读）" />
      )}
      <Card
        title="运行参数"
        extra={
          <Space wrap>
            <Button
              icon={<ReloadOutlined />}
              disabled={!isAdmin || readOnly || rebuilding}
              loading={rebuilding}
              onClick={() => void updateEngineTree()}
            >
              更新 AI 数据引擎-智慧树
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              disabled={!isAdmin || readOnly || !dirty || rebuilding}
              loading={saving}
              onClick={() => void save()}
            >
              保存并热加载
            </Button>
          </Space>
        }
      >
        {rebuildStatus && <Typography.Text type="secondary">{rebuildStatus}；此过程会重新生成真实拓扑节点，请保持页面打开。</Typography.Text>}
        <Table
          rowKey="key"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={items}
          pagination={false}
        />
      </Card>
    </Space>
  );
}
