import {
  CheckCircleOutlined,
  CopyOutlined,
  EditOutlined,
  ExperimentOutlined,
  InboxOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  SaveOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Divider,
  Drawer,
  Empty,
  Input,
  message,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  analyzeAgentSkill,
  archiveAgentSkill,
  getAgentSkill,
  listAgentSkills,
  publishAgentSkill,
  setAgentSkillStatus,
  updateAgentSessionSkills,
  validateAgentSkill,
} from "../api/endpoints";
import { errorMessage } from "../api/client";
import type {
  AgentSession,
  AgentSkillDraft,
  AgentSkillMode,
  AgentSkillSummary,
  AgentSkillValidation,
} from "../api/types";

const { Text, Title, Paragraph } = Typography;
const EMPTY_DRAFT: AgentSkillDraft = {
  name: "",
  display_name: "",
  description: "",
  instructions: "",
};

interface SkillCenterProps {
  open: boolean;
  session: AgentSession | null;
  modelId: string;
  running: boolean;
  onClose: () => void;
  onSessionChange: (session: AgentSession) => void;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

export default function SkillCenter({
  open,
  session,
  modelId,
  running,
  onClose,
  onSessionChange,
}: SkillCenterProps) {
  const [messageApi, messageContext] = message.useMessage();
  const [modalApi, modalContext] = Modal.useModal();
  const [tab, setTab] = useState("session");
  const [skills, setSkills] = useState<AgentSkillSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<AgentSkillMode>("auto");
  const [selected, setSelected] = useState<string[]>([]);
  const [savingSession, setSavingSession] = useState(false);
  const [idea, setIdea] = useState("");
  const [includeContext, setIncludeContext] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [draft, setDraft] = useState<AgentSkillDraft | null>(null);
  const [validation, setValidation] = useState<AgentSkillValidation | null>(null);
  const [validating, setValidating] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [rowAction, setRowAction] = useState<string | null>(null);

  const enabledSkills = useMemo(() => skills.filter((item) => item.enabled), [skills]);

  const refreshSkills = async () => {
    setLoading(true);
    try {
      setSkills(await listAgentSkills());
    } catch (cause) {
      messageApi.error(errorMessage(cause, "无法读取 Skill 列表"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    setMode(session?.skill_mode || "auto");
    setSelected(session?.selected_skills || []);
    void refreshSkills();
  }, [open, session?.id]);

  const resetSessionRuntime = async (
    nextSelected = selected,
    nextMode = mode,
  ) => {
    if (!session) return;
    try {
      const updated = await updateAgentSessionSkills(
        session.id,
        nextMode,
        nextMode === "manual" ? nextSelected : [],
      );
      onSessionChange(updated);
    } catch (cause) {
      messageApi.warning(
        errorMessage(cause, "Skill 已更新，但会话运行时刷新失败，请重新保存会话配置"),
      );
    }
  };

  const saveSessionSkills = async () => {
    if (!session) return;
    setSavingSession(true);
    try {
      const updated = await updateAgentSessionSkills(
        session.id,
        mode,
        mode === "manual" ? selected : [],
      );
      onSessionChange(updated);
      messageApi.success("会话 Skill 配置已保存，下次运行生效");
    } catch (cause) {
      messageApi.error(errorMessage(cause, "无法保存会话 Skill 配置"));
    } finally {
      setSavingSession(false);
    }
  };

  const generateDraft = async () => {
    if (!idea.trim()) {
      messageApi.warning("请先说明希望 Skill 完成什么任务");
      return;
    }
    if (!modelId) {
      messageApi.warning("请先选择可用模型");
      return;
    }
    setGenerating(true);
    setValidation(null);
    try {
      const result = await analyzeAgentSkill(
        idea.trim(),
        modelId,
        session?.id,
        includeContext,
      );
      setDraft(result.draft);
      setValidation(result.validation);
      messageApi.success("已生成待确认草稿，请检查并修改");
    } catch (cause) {
      messageApi.error(errorMessage(cause, "AI 生成 Skill 草稿失败"));
    } finally {
      setGenerating(false);
    }
  };

  const runValidation = async () => {
    if (!draft) return null;
    setValidating(true);
    try {
      const result = await validateAgentSkill(draft);
      setValidation(result);
      if (result.valid) messageApi.success(result.warnings.length ? "校验通过，但有需要确认的提醒" : "校验通过");
      else messageApi.error("校验未通过，请修正标红问题");
      return result;
    } catch (cause) {
      messageApi.error(errorMessage(cause, "Skill 校验失败"));
      return null;
    } finally {
      setValidating(false);
    }
  };

  const confirmPublish = async () => {
    if (!draft) return;
    const result = await runValidation();
    if (!result?.valid) return;
    modalApi.confirm({
      title: draft.expected_version ? "确认更新 Skill？" : "确认发布 Skill？",
      icon: <SafetyCertificateOutlined />,
      width: 560,
      content: (
        <div className="skill-confirm-content">
          <Paragraph>
            发布后 Harness 可以发现此 Skill。模型仍然保持只读，只有受控管理器会写入专用 Skill 目录。
          </Paragraph>
          {result.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="请确认以下提醒"
              description={result.warnings.map((item) => <div key={item}>{item}</div>)}
            />
          )}
        </div>
      ),
      okText: draft.expected_version ? "确认更新" : "确认发布",
      cancelText: "继续修改",
      async onOk() {
        setPublishing(true);
        try {
          const saved = await publishAgentSkill({
            ...draft,
            confirmed: true,
            acknowledge_warnings: true,
          });
          setDraft({
            name: saved.name,
            display_name: saved.display_name,
            description: saved.description,
            instructions: saved.instructions,
            expected_version: saved.version,
          });
          await refreshSkills();
          await resetSessionRuntime();
          messageApi.success(`Skill “${saved.display_name}”已发布`);
        } catch (cause) {
          messageApi.error(errorMessage(cause, "Skill 发布失败"));
          throw cause;
        } finally {
          setPublishing(false);
        }
      },
    });
  };

  const loadDraft = async (item: AgentSkillSummary, copy = false) => {
    setRowAction(item.name);
    try {
      const detail = await getAgentSkill(item.name);
      setDraft({
        name: copy ? `${detail.name}-copy` : detail.name,
        display_name: copy ? `${detail.display_name}副本` : detail.display_name,
        description: detail.description,
        instructions: detail.instructions,
        expected_version: copy ? undefined : detail.version,
      });
      setIdea("");
      setValidation(null);
      setTab("generate");
    } catch (cause) {
      messageApi.error(errorMessage(cause, "无法读取 Skill"));
    } finally {
      setRowAction(null);
    }
  };

  const toggleSkill = async (item: AgentSkillSummary, enabled: boolean) => {
    setRowAction(item.name);
    try {
      await setAgentSkillStatus(item.name, enabled);
      const nextSelected = enabled
        ? selected
        : selected.filter((name) => name !== item.name);
      if (!enabled) setSelected(nextSelected);
      await refreshSkills();
      await resetSessionRuntime(nextSelected);
      messageApi.success(enabled ? "Skill 已启用" : "Skill 已停用，可随时恢复");
    } catch (cause) {
      messageApi.error(errorMessage(cause, "无法更新 Skill 状态"));
    } finally {
      setRowAction(null);
    }
  };

  const archiveSkill = async (item: AgentSkillSummary) => {
    setRowAction(item.name);
    try {
      await archiveAgentSkill(item.name);
      const nextSelected = selected.filter((name) => name !== item.name);
      setSelected(nextSelected);
      await refreshSkills();
      await resetSessionRuntime(nextSelected);
      messageApi.success("Skill 已归档，服务器保留可恢复版本");
    } catch (cause) {
      messageApi.error(errorMessage(cause, "无法归档 Skill"));
    } finally {
      setRowAction(null);
    }
  };

  const updateDraft = (patch: Partial<AgentSkillDraft>) => {
    setDraft((current) => ({ ...(current || EMPTY_DRAFT), ...patch }));
    setValidation(null);
  };

  const sessionPanel = (
    <div className="skill-center-panel">
      <Alert
        showIcon
        type="info"
        message="决定当前会话怎样使用 Skill"
        description="自动匹配适合日常提问；指定加载只向本次运行暴露你勾选的 Skill。切换后会新建 Harness 上下文，但保留当前聊天记录。"
      />
      <div className="skill-setting-row">
        <div>
          <Text strong>加载方式</Text>
          <Text type="secondary">自动发现或固定会话能力</Text>
        </div>
        <Segmented
          value={mode}
          onChange={(value) => setMode(value as AgentSkillMode)}
          options={[
            { label: "自动匹配", value: "auto" },
            { label: "指定加载", value: "manual" },
          ]}
          disabled={running}
        />
      </div>
      <div className="skill-field">
        <Text strong>本会话使用的 Skill</Text>
        <Select
          mode="multiple"
          value={selected}
          onChange={setSelected}
          disabled={mode !== "manual" || running}
          placeholder={enabledSkills.length ? "选择一个或多个 Skill" : "暂无已启用 Skill"}
          options={enabledSkills.map((item) => ({
            value: item.name,
            label: `${item.display_name} · v${item.version}`,
          }))}
        />
        {mode === "manual" && !selected.length && (
          <Text type="secondary">未选择时仅使用通用 Agent，不会自动加载其他 Skill。</Text>
        )}
      </div>
      <Button
        type="primary"
        icon={<SaveOutlined />}
        loading={savingSession}
        disabled={!session || running}
        onClick={() => void saveSessionSkills()}
      >
        保存会话配置
      </Button>
    </div>
  );

  const generationPanel = (
    <div className="skill-center-panel">
      <div className="skill-field">
        <Text strong>你希望 Agent 反复掌握什么能力？</Text>
        <Input.TextArea
          rows={4}
          value={idea}
          maxLength={4000}
          showCount
          placeholder="例如：把当前已经验证成功的月度业务趋势分析流程沉淀为 Skill，始终先确认数据口径，再生成表格和折线图。"
          onChange={(event) => setIdea(event.target.value)}
        />
        <Checkbox checked={includeContext} onChange={(event) => setIncludeContext(event.target.checked)}>
          参考当前会话中的成功过程和修正意见
        </Checkbox>
      </div>
      <Button
        type="primary"
        icon={<ExperimentOutlined />}
        loading={generating}
        disabled={!modelId || running}
        onClick={() => void generateDraft()}
      >
        AI 生成待确认草稿
      </Button>

      {draft && (
        <>
          <Divider />
          <div className="skill-draft-heading">
            <div>
              <Title level={5}>待确认 Skill 草稿</Title>
              <Text type="secondary">下面所有内容都可以修改，确认发布前不会写入启用目录。</Text>
            </div>
            <Tag color="gold">pending_review</Tag>
          </div>
          <div className="skill-draft-grid">
            <label className="skill-field">
              <Text strong>内部名称</Text>
              <Input
                value={draft.name}
                disabled={Boolean(draft.expected_version)}
                placeholder="monthly-sales-trend"
                onChange={(event) => updateDraft({ name: event.target.value.toLowerCase() })}
              />
              <Text type="secondary">仅小写英文、数字和连字符；已发布 Skill 更新时不能改名。</Text>
            </label>
            <label className="skill-field">
              <Text strong>用户显示名</Text>
              <Input
                value={draft.display_name}
                placeholder="月度业务趋势分析"
                onChange={(event) => updateDraft({ display_name: event.target.value })}
              />
            </label>
          </div>
          <label className="skill-field">
            <Text strong>触发说明</Text>
            <Input.TextArea
              rows={3}
              value={draft.description}
              maxLength={500}
              showCount
              onChange={(event) => updateDraft({ description: event.target.value })}
            />
            <Text type="secondary">说明“做什么、何时使用、什么时候不适用”，用于自动匹配。</Text>
          </label>
          <label className="skill-field">
            <Text strong>Skill 指令</Text>
            <Input.TextArea
              className="skill-instructions-editor"
              rows={18}
              value={draft.instructions}
              maxLength={20_000}
              showCount
              onChange={(event) => updateDraft({ instructions: event.target.value })}
            />
          </label>
          {validation && (
            <Alert
              showIcon
              type={validation.valid ? (validation.warnings.length ? "warning" : "success") : "error"}
              message={validation.valid ? "格式校验通过" : "格式校验未通过"}
              description={(
                <div>
                  {validation.errors.map((item) => <div key={`error-${item}`}>{item}</div>)}
                  {validation.warnings.map((item) => <div key={`warning-${item}`}>{item}</div>)}
                </div>
              )}
            />
          )}
          <Space wrap>
            <Button
              icon={<CheckCircleOutlined />}
              loading={validating}
              onClick={() => void runValidation()}
            >
              校验草稿
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={publishing}
              disabled={running}
              onClick={() => void confirmPublish()}
            >
              {draft.expected_version ? "确认更新" : "确认发布"}
            </Button>
          </Space>
        </>
      )}
    </div>
  );

  const installedPanel = loading ? <Spin className="agent-center" /> : skills.length ? (
    <Table<AgentSkillSummary>
      className="skill-table"
      rowKey="name"
      size="small"
      pagination={{ pageSize: 8, hideOnSinglePage: true }}
      scroll={{ x: 820 }}
      dataSource={skills}
      columns={[
        {
          title: "Skill",
          dataIndex: "display_name",
          width: 210,
          render: (_, item) => (
            <div className="skill-name-cell">
              <Text strong>{item.display_name}</Text>
              <Text type="secondary">{item.name}</Text>
            </div>
          ),
        },
        {
          title: "触发说明",
          dataIndex: "description",
          ellipsis: true,
          width: 300,
        },
        {
          title: "状态",
          dataIndex: "enabled",
          width: 120,
          render: (enabled, item) => (
            <Space size={6}>
              <Switch
                size="small"
                checked={enabled}
                loading={rowAction === item.name}
                disabled={running}
                checkedChildren={<PlayCircleOutlined />}
                unCheckedChildren={<PauseCircleOutlined />}
                onChange={(checked) => void toggleSkill(item, checked)}
              />
              <Tag color={enabled ? "green" : "default"}>{enabled ? "已启用" : "已停用"}</Tag>
            </Space>
          ),
        },
        {
          title: "版本",
          dataIndex: "version",
          width: 78,
          render: (version) => `v${version}`,
        },
        {
          title: "更新时间",
          dataIndex: "updated_at",
          width: 170,
          render: formatTime,
        },
        {
          title: "操作",
          key: "actions",
          fixed: "right",
          width: 150,
          render: (_, item) => (
            <Space size={0}>
              <Tooltip title="编辑">
                <Button type="text" icon={<EditOutlined />} loading={rowAction === item.name} onClick={() => void loadDraft(item)} />
              </Tooltip>
              <Tooltip title="复制为新草稿">
                <Button type="text" icon={<CopyOutlined />} onClick={() => void loadDraft(item, true)} />
              </Tooltip>
              <Popconfirm
                title="归档这个 Skill？"
                description="不会物理删除，服务器会保留可恢复版本。"
                okText="归档"
                cancelText="取消"
                onConfirm={() => archiveSkill(item)}
              >
                <Tooltip title="归档">
                  <Button type="text" danger disabled={running} icon={<InboxOutlined />} />
                </Tooltip>
              </Popconfirm>
            </Space>
          ),
        },
      ]}
    />
  ) : (
    <Empty description="暂无 Skill，可在“AI 生成”中创建第一个" />
  );

  return (
    <Drawer
      className="skill-center-drawer"
      title="Skill 中心"
      placement="right"
      width="min(920px, 96vw)"
      open={open}
      onClose={onClose}
      extra={<Tag color="blue">受控写入</Tag>}
    >
      {messageContext}
      {modalContext}
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          { key: "session", label: "会话加载", children: sessionPanel },
          { key: "generate", label: "AI 生成", children: generationPanel },
          { key: "installed", label: `已安装 ${skills.length}`, children: installedPanel },
        ]}
      />
    </Drawer>
  );
}
