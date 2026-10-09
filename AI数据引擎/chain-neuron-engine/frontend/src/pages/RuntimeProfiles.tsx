import {
  CheckCircleOutlined,
  CloudSyncOutlined,
  DeploymentUnitOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";

import { errorMessage } from "../api/client";
import {
  activateRuntimeProfile,
  createRuntimeProfile,
  fetchRuntimeProfileAudit,
  fetchRuntimeProfiles,
  readyRuntimeProfile,
  updateRuntimeProfile,
  verifyRuntimeProfile,
} from "../api/endpoints";
import type {
  RuntimeAuditEvent,
  RuntimeProfile,
  RuntimeProfilesResponse,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import "./RuntimeProfiles.css";

type DatasetBindingRow = { logical_id: string; generation_id: string };

interface ProfileFormValues {
  profile_id: string;
  display_name: string;
  environment: string;
  storage_type: string;
  adapter_version: string;
  storage_root: string;
  catalog_id: string;
  catalog_hash: string;
  datasets: DatasetBindingRow[];
  reason: string;
}

const REQUIRED_DATASETS = [
  "oil_master",
  "non_oil_sales",
  "oil_sales_plan",
  "oil_sales_daily",
  "oil_monthly_sales",
];

const STATE_META: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  building: { text: "构建中", color: "processing" },
  verifying: { text: "验收中", color: "blue" },
  ready: { text: "已就绪", color: "green" },
  active: { text: "当前运行", color: "success" },
  retired: { text: "已退役", color: "default" },
};

const ACCEPTANCE_META: Record<string, { text: string; color: string }> = {
  not_run: { text: "未验收", color: "default" },
  running: { text: "验收中", color: "processing" },
  passed: { text: "已通过", color: "success" },
  failed: { text: "未通过", color: "error" },
};

function stateTag(profile: RuntimeProfile) {
  const meta = STATE_META[profile.effective_state] || {
    text: profile.effective_state,
    color: "default",
  };
  return <Tag color={meta.color}>{meta.text}</Tag>;
}

function acceptanceTag(profile: RuntimeProfile) {
  const meta = ACCEPTANCE_META[profile.acceptance.status] || {
    text: profile.acceptance.status,
    color: "default",
  };
  return <Tag color={meta.color}>{meta.text}</Tag>;
}

export default function RuntimeProfilesPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [form] = Form.useForm<ProfileFormValues>();
  const [data, setData] = useState<RuntimeProfilesResponse | null>(null);
  const [audit, setAudit] = useState<RuntimeAuditEvent[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<RuntimeProfile | null>(null);
  const [activateTarget, setActivateTarget] = useState<RuntimeProfile | null>(null);
  const [activateConfirm, setActivateConfirm] = useState("");
  const [activateReason, setActivateReason] = useState("");

  const selected = useMemo(
    () => data?.items.find((item) => item.profile_id === selectedId) || data?.active || null,
    [data, selectedId],
  );

  const load = async () => {
    setLoading(true);
    try {
      const [profiles, events] = await Promise.all([
        fetchRuntimeProfiles(),
        fetchRuntimeProfileAudit(),
      ]);
      setData(profiles);
      setAudit(events);
      setSelectedId((current) =>
        profiles.items.some((item) => item.profile_id === current)
          ? current
          : profiles.active.profile_id,
      );
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const run = async (key: string, action: () => Promise<unknown>, success: string) => {
    setWorking(key);
    try {
      await action();
      message.success(success);
      await load();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setWorking("");
    }
  };

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      profile_id: "",
      display_name: "",
      environment: "test",
      storage_type: "cnts",
      adapter_version: "1.0.0",
      storage_root: "",
      catalog_id: "",
      catalog_hash: "",
      datasets: REQUIRED_DATASETS.map((logical_id) => ({ logical_id, generation_id: "" })),
      reason: "创建候选运行配置档",
    });
    setEditorOpen(true);
  };

  const openEdit = (profile: RuntimeProfile) => {
    setEditing(profile);
    form.setFieldsValue({
      profile_id: profile.profile_id,
      display_name: profile.display_name,
      environment: profile.environment,
      storage_type: profile.storage.type,
      adapter_version: profile.storage.adapter_version,
      storage_root: profile.storage.root,
      catalog_id: profile.catalog.catalog_id,
      catalog_hash: profile.catalog.catalog_hash,
      datasets: Object.entries(profile.catalog.datasets).map(
        ([logical_id, generation_id]) => ({ logical_id, generation_id }),
      ),
      reason: "修改候选运行配置档",
    });
    setEditorOpen(true);
  };

  const saveEditor = async () => {
    const values = await form.validateFields();
    const datasets = Object.fromEntries(
      values.datasets.map((row) => [row.logical_id.trim(), row.generation_id.trim()]),
    );
    const payload = {
      profile_id: values.profile_id.trim(),
      display_name: values.display_name.trim(),
      environment: values.environment,
      storage: {
        type: values.storage_type,
        adapter_version: values.adapter_version.trim(),
        root: values.storage_root.trim(),
        contract_version: 1,
      },
      catalog: {
        catalog_id: values.catalog_id.trim(),
        catalog_hash: values.catalog_hash.trim(),
        datasets,
      },
      semantics: editing?.semantics || {},
      topology: editing?.topology || { schema_version: 1 },
      reason: values.reason.trim(),
    };
    setWorking("save");
    try {
      if (editing) {
        await updateRuntimeProfile(editing.profile_id, {
          ...payload,
          profile_id: undefined,
          expected_version: editing.profile_version,
        });
      } else {
        await createRuntimeProfile(payload);
      }
      setEditorOpen(false);
      message.success(editing ? "候选配置档已更新，需重新验收" : "候选配置档已创建");
      await load();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setWorking("");
    }
  };

  const profileColumns: ColumnsType<RuntimeProfile> = [
    {
      title: "运行配置档",
      dataIndex: "display_name",
      render: (_, profile) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{profile.display_name}</Typography.Text>
          <Typography.Text type="secondary" className="runtime-profile-code">
            {profile.profile_id} · v{profile.profile_version}
          </Typography.Text>
        </Space>
      ),
    },
    { title: "环境", dataIndex: "environment", width: 110 },
    {
      title: "存储",
      width: 150,
      render: (_, profile) => (
        <Space direction="vertical" size={0}>
          <Tag color={profile.storage.type === "cnts" ? "purple" : "blue"}>
            {profile.storage.type === "cnts" ? "CNTS" : "Legacy SQLite"}
          </Tag>
          <Typography.Text type="secondary">适配器 {profile.storage.adapter_version}</Typography.Text>
        </Space>
      ),
    },
    { title: "状态", width: 110, render: (_, profile) => stateTag(profile) },
    { title: "验收", width: 110, render: (_, profile) => acceptanceTag(profile) },
    {
      title: "数据集",
      width: 90,
      render: (_, profile) => Object.keys(profile.catalog.datasets).length,
    },
    {
      title: "操作",
      width: 330,
      render: (_, profile) => (
        <Space wrap>
          <Button size="small" onClick={() => setSelectedId(profile.profile_id)}>查看</Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            disabled={!isAdmin || !["draft", "building", "verifying"].includes(profile.state)}
            onClick={() => openEdit(profile)}
          >
            修改
          </Button>
          <Button
            size="small"
            icon={<SafetyCertificateOutlined />}
            loading={working === `verify:${profile.profile_id}`}
            disabled={!isAdmin || !["draft", "building", "verifying"].includes(profile.state)}
            onClick={() => void run(
              `verify:${profile.profile_id}`,
              () => verifyRuntimeProfile(profile.profile_id, profile.profile_version, "管理员执行候选配置档验收"),
              "验收执行完成，请查看验收状态",
            )}
          >
            执行验收
          </Button>
          <Button
            size="small"
            icon={<CheckCircleOutlined />}
            loading={working === `ready:${profile.profile_id}`}
            disabled={!isAdmin || profile.state !== "verifying" || profile.acceptance.status !== "passed"}
            onClick={() => void run(
              `ready:${profile.profile_id}`,
              () => readyRuntimeProfile(profile.profile_id, profile.profile_version, "锁定已通过验收的候选配置档"),
              "配置档已锁定为 ready",
            )}
          >
            锁定
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<SwapOutlined />}
            disabled={!isAdmin || profile.is_active || profile.effective_state !== "ready"}
            onClick={() => {
              setActivateTarget(profile);
              setActivateConfirm("");
              setActivateReason("");
            }}
          >
            激活
          </Button>
        </Space>
      ),
    },
  ];

  const datasetRows = selected
    ? Object.entries(selected.catalog.datasets).map(([logical_id, generation_id]) => ({ logical_id, generation_id }))
    : [];

  const tabs = [
    {
      key: "profiles",
      label: "运行配置档",
      children: (
        <Table
          rowKey="profile_id"
          loading={loading}
          columns={profileColumns}
          dataSource={data?.items || []}
          pagination={false}
          scroll={{ x: 1120 }}
        />
      ),
    },
    {
      key: "datasets",
      label: "数据集绑定",
      children: (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Select
            value={selected?.profile_id}
            style={{ width: "min(100%, 420px)" }}
            options={(data?.items || []).map((profile) => ({
              value: profile.profile_id,
              label: `${profile.display_name} · v${profile.profile_version}`,
            }))}
            onChange={setSelectedId}
          />
          <Table
            rowKey="logical_id"
            size="middle"
            pagination={false}
            dataSource={datasetRows}
            columns={[
              { title: "逻辑数据集", dataIndex: "logical_id" },
              {
                title: "Generation",
                dataIndex: "generation_id",
                render: (value: string) => <span className="runtime-profile-code">{value}</span>,
              },
              { title: "Catalog", render: () => selected?.catalog.catalog_id || "-" },
              { title: "Catalog 指纹", render: () => selected?.catalog.catalog_hash || "-" },
            ]}
          />
        </Space>
      ),
    },
    {
      key: "acceptance",
      label: "上线验收",
      children: selected ? (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type={selected.acceptance.status === "passed" ? "success" : selected.acceptance.status === "failed" ? "error" : "info"}
            showIcon
            message={`验收状态：${ACCEPTANCE_META[selected.acceptance.status]?.text || selected.acceptance.status}`}
            description={selected.acceptance.report_id
              ? `报告：${selected.acceptance.report_id}；通过时间：${selected.acceptance.passed_at || "-"}`
              : "尚未生成验收报告。CNTS 只有在完整适配器和全量对账通过后才能锁定。"}
          />
          <Table
            rowKey="name"
            pagination={false}
            dataSource={Object.entries(selected.acceptance.checks || {}).map(([name, value]) => ({
              name,
              value: JSON.stringify(value, null, 2),
            }))}
            columns={[
              { title: "检查项", dataIndex: "name", width: 220 },
              {
                title: "结果",
                dataIndex: "value",
                render: (value: string) => <Typography.Text className="runtime-profile-code">{value}</Typography.Text>,
              },
            ]}
          />
        </Space>
      ) : null,
    },
    {
      key: "history",
      label: "切换记录",
      children: (
        <Table
          rowKey="event_id"
          size="middle"
          dataSource={audit}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: "时间", dataIndex: "timestamp", width: 220 },
            { title: "动作", dataIndex: "action", width: 120 },
            { title: "操作者", dataIndex: "actor", width: 180 },
            { title: "原因", dataIndex: "reason" },
            { title: "指针版本", dataIndex: "pointer_revision", width: 100 },
          ]}
        />
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="warning"
        showIcon
        message="切换的是完整的存储与数据集运行配置档"
        description="Agent 地址、统一查询协议、基座、中通周存、DNA 双链、生长和聚合语义保持不变。系统不会在 CNTS 故障或无命中时自动回退 Legacy SQLite。"
      />

      {data?.active && (
        <Card
          title="当前运行配置"
          extra={stateTag(data.active)}
        >
          <div className="runtime-profile-summary">
            <div className="runtime-profile-summary__item">
              <Typography.Text type="secondary">配置档</Typography.Text>
              <span className="runtime-profile-summary__value">{data.active.display_name}</span>
            </div>
            <div className="runtime-profile-summary__item">
              <Typography.Text type="secondary">存储执行器</Typography.Text>
              <span className="runtime-profile-summary__value">{data.active.storage.type}</span>
            </div>
            <div className="runtime-profile-summary__item">
              <Typography.Text type="secondary">Catalog</Typography.Text>
              <span className="runtime-profile-summary__value runtime-profile-code">{data.active.catalog.catalog_id}</span>
            </div>
            <div className="runtime-profile-summary__item">
              <Typography.Text type="secondary">活动指针</Typography.Text>
              <span className="runtime-profile-summary__value">revision {data.pointer.revision}</span>
            </div>
          </div>
        </Card>
      )}

      {!isAdmin && <Alert type="info" showIcon message="当前为只读视图，只有管理员可以验收和切换配置档" />}

      <Card
        title="AI 引擎与数据集"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} disabled={!isAdmin} onClick={openCreate}>添加候选配置</Button>
          </Space>
        }
      >
        <Tabs items={tabs} />
      </Card>

      <Modal
        title={editing ? "修改候选运行配置档" : "添加候选运行配置档"}
        open={editorOpen}
        width={860}
        okText="保存候选配置"
        cancelText="取消"
        confirmLoading={working === "save"}
        onOk={() => void saveEditor()}
        onCancel={() => setEditorOpen(false)}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          message="保存不会切换生产"
          description="候选配置必须依次完成验收、锁定和人工激活。修改后原验收结果自动失效。"
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical">
          <Space align="start" wrap style={{ width: "100%" }}>
            <Form.Item name="profile_id" label="配置档 id" rules={[{ required: true }]}>
              <Input disabled={Boolean(editing)} style={{ width: 250 }} placeholder="cnts-prod-v1" />
            </Form.Item>
            <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}>
              <Input style={{ width: 260 }} placeholder="CNTS 生产 V1" />
            </Form.Item>
            <Form.Item name="environment" label="环境" rules={[{ required: true }]}>
              <Select style={{ width: 160 }} options={[
                { value: "test", label: "测试" },
                { value: "staging", label: "预生产" },
                { value: "production", label: "生产" },
                { value: "local", label: "本机" },
              ]} />
            </Form.Item>
          </Space>
          <Space align="start" wrap style={{ width: "100%" }}>
            <Form.Item name="storage_type" label="存储类型" rules={[{ required: true }]}>
              <Select style={{ width: 180 }} options={[
                { value: "cnts", label: "CNTS 自研二进制" },
                { value: "legacy_sqlite", label: "Legacy SQLite" },
              ]} />
            </Form.Item>
            <Form.Item name="adapter_version" label="适配器版本" rules={[{ required: true }]}>
              <Input style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="storage_root" label="存储根目录/数据库文件" rules={[{ required: true }]}>
              <Input style={{ width: 410 }} placeholder="允许数据目录内的路径" />
            </Form.Item>
          </Space>
          <Space align="start" wrap style={{ width: "100%" }}>
            <Form.Item name="catalog_id" label="Catalog id" rules={[{ required: true }]}>
              <Input style={{ width: 300 }} />
            </Form.Item>
            <Form.Item name="catalog_hash" label="Catalog 指纹" rules={[{ required: true }]}>
              <Input style={{ width: 430 }} placeholder="sha256:..." />
            </Form.Item>
          </Space>
          <Typography.Title level={5}>数据集绑定</Typography.Title>
          <Form.List name="datasets">
            {(fields, { add, remove }) => (
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" wrap>
                    <Form.Item {...field} name={[field.name, "logical_id"]} rules={[{ required: true, message: "填写逻辑数据集" }]}>
                      <Input style={{ width: 260 }} placeholder="逻辑数据集 id" />
                    </Form.Item>
                    <Form.Item {...field} name={[field.name, "generation_id"]} rules={[{ required: true, message: "填写 generation" }]}>
                      <Input style={{ width: 380 }} placeholder="不可变 generation id" />
                    </Form.Item>
                    <Button danger onClick={() => remove(field.name)}>删除</Button>
                  </Space>
                ))}
                <Button icon={<DeploymentUnitOutlined />} onClick={() => add({ logical_id: "", generation_id: "" })}>添加数据集</Button>
              </Space>
            )}
          </Form.List>
          <Form.Item name="reason" label="变更原因" rules={[{ required: true, min: 2 }]} style={{ marginTop: 16 }}>
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="激活完整运行配置档"
        open={Boolean(activateTarget)}
        okText="确认原子切换"
        cancelText="取消"
        okButtonProps={{
          danger: true,
          disabled: activateConfirm !== activateTarget?.profile_id || activateReason.trim().length < 2,
          icon: <CloudSyncOutlined />,
        }}
        confirmLoading={working === "activate"}
        onCancel={() => setActivateTarget(null)}
        onOk={() => {
          if (!activateTarget || !data) return;
          void run(
            "activate",
            () => activateRuntimeProfile(
              activateTarget.profile_id,
              data.pointer.revision,
              activateReason.trim(),
            ),
            "运行配置档已原子激活，新请求将使用新存储和数据集目录",
          ).then(() => setActivateTarget(null));
        }}
      >
        <Alert
          type="warning"
          showIcon
          message="系统不会自动回退"
          description="激活后所有新查询只访问该配置档。故障时需要管理员人工激活历史完整配置档。"
          style={{ marginBottom: 16 }}
        />
        <Typography.Paragraph>
          目标：<Typography.Text strong>{activateTarget?.display_name}</Typography.Text>
        </Typography.Paragraph>
        <Input
          value={activateConfirm}
          onChange={(event) => setActivateConfirm(event.target.value)}
          placeholder={`输入 ${activateTarget?.profile_id || "配置档 id"} 确认`}
          style={{ marginBottom: 12 }}
        />
        <Input.TextArea
          value={activateReason}
          onChange={(event) => setActivateReason(event.target.value)}
          placeholder="填写切换原因"
          rows={3}
        />
      </Modal>
    </Space>
  );
}
