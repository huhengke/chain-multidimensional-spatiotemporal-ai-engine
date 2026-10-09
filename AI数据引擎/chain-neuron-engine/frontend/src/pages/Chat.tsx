import {
  BranchesOutlined,
  ArrowUpOutlined,
  CloseOutlined,
  CopyOutlined,
  DeleteOutlined,
  DislikeFilled,
  DislikeOutlined,
  EditOutlined,
  FullscreenOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  InboxOutlined,
  LikeFilled,
  LikeOutlined,
  MoreOutlined,
  PaperClipOutlined,
  PlusOutlined,
  PushpinFilled,
  PushpinOutlined,
  RobotOutlined,
  ReloadOutlined,
  StopOutlined,
  SyncOutlined,
  ToolOutlined,
  UndoOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Bubble, Sender, XProvider } from "@ant-design/x";
import {
  Alert,
  Button,
  Dropdown,
  Empty,
  Input,
  List,
  message,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  archiveAgentSession,
  branchAgentSession,
  cancelAgentRun,
  createAgentSession,
  deleteAgentSession,
  getAgentRun,
  getAgentSession,
  agentAttachmentContentUrl,
  deleteAgentAttachmentDraft,
  fetchAgentAttachmentLimits,
  listAgentModels,
  listAgentSessions,
  pinAgentSession,
  renameAgentSession,
  sendAgentMessage,
  setAgentMessageFeedback,
  uploadAgentAttachment,
} from "../api/endpoints";
import type { AgentAttachment, AgentAttachmentLimits, AgentMessage, AgentModel, AgentRun, AgentRunEvent, AgentSession } from "../api/types";
import { errorMessage } from "../api/client";
import AgentMessageContent from "../components/AgentMessageContent";
import SkillCenter from "../components/SkillCenter";
import "./Agent.css";

const { Text } = Typography;
const SUGGESTIONS = [
  "查询2024年各月业务变化，并概括最高和最低月份",
  "查看引擎中有哪些可用数据集及其查询能力",
  "分析2024年业务情况，并用表格展示主要结果",
];

const ACCEPTED_ATTACHMENTS = ".png,.jpg,.jpeg,.webp,.gif,.pdf,.doc,.docx,.xls,.xlsx";
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);
const DOCUMENT_EXTENSIONS = new Set(["pdf", "doc", "docx", "xls", "xlsx"]);
const DEFAULT_ATTACHMENT_LIMITS: AgentAttachmentLimits = {
  max_attachments: 4,
  max_documents: 2,
  image_max_bytes: 20 * 1024 * 1024,
  document_max_bytes: 50 * 1024 * 1024,
  pdf_max_pages: 200,
  workbook_max_sheets: 50,
  workbook_max_cells: 200_000,
};

interface DraftAttachment {
  localId: string;
  sessionId: string;
  file: File;
  token?: string;
  attachment?: AgentAttachment;
  status: "uploading" | "ready" | "error";
  progress: number;
  error?: string;
}

function extension(name: string): string {
  return name.toLowerCase().split(".").pop() || "";
}

function isImageFile(file: File): boolean {
  return IMAGE_EXTENSIONS.has(extension(file.name));
}

function isDocumentFile(file: File): boolean {
  return DOCUMENT_EXTENSIONS.has(extension(file.name));
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function attachmentIcon(item: Pick<AgentAttachment, "kind" | "document_type">) {
  if (item.kind === "image") return <FileImageOutlined />;
  if (item.document_type === "pdf") return <FilePdfOutlined />;
  if (item.kind === "workbook") return <FileExcelOutlined />;
  return <FileWordOutlined />;
}

function attachmentDetail(item: AgentAttachment): string {
  if (item.page_count) return `${item.page_count} 页`;
  if (item.sheet_count) return `${item.sheet_count} 个工作表 · ${Number(item.non_empty_cells || 0).toLocaleString()} 个非空单元格`;
  return formatBytes(item.size);
}

function eventText(item: AgentRunEvent): string {
  if (item.type === "run.started") return "运行已开始";
  if (item.type === "planner.mode.selected") return item.message || "使用通用 Agent 规划";
  if (item.type === "planner.mode.changed") return item.message || "已加载匹配 Skill";
  if (item.type === "run.completed") return "运行完成";
  if (item.type === "run.failed") return item.error?.message || "运行失败";
  if (item.type === "run.cancelled") return "运行已取消";
  if (item.type === "agent.chart.result") return "交互式图表已生成";
  if (item.type === "harness.event") {
    const raw = item.event || {};
    const type = String(raw.type || "Harness");
    if (type === "session") return "Harness 会话已连接";
    if (type === "status") {
      const phase = String(raw.phase || "running");
      const labels: Record<string, string> = {
        turn_start: "Agent 开始处理",
        step_start: "正在规划或调用工具",
        step_end: "当前步骤完成",
        turn_end: "Agent 本轮结束",
      };
      return labels[phase] || phase;
    }
    if (type === "final") return "最终回答已生成";
    return type;
  }
  return item.type;
}

function timelineColor(item: AgentRunEvent): string {
  if (item.type === "run.failed") return "red";
  if (item.type === "run.cancelled") return "orange";
  if (item.type === "run.completed") return "green";
  if (item.type.includes("planner")) return "blue";
  return "gray";
}

function formatDuration(milliseconds?: number | null): string {
  if (milliseconds == null) return "";
  if (milliseconds < 1000) return `${milliseconds} 毫秒`;
  return `${(milliseconds / 1000).toFixed(1)} 秒`;
}

function MessageAttachments({ sessionId, items }: { sessionId: string; items: AgentAttachment[] }) {
  if (!items.length) return null;
  return (
    <div className="agent-message-attachments">
      {items.map((item) => {
        const url = agentAttachmentContentUrl(sessionId, item.id);
        return (
          <a
            key={`${item.id}-${item.name}`}
            className={`agent-message-attachment ${item.kind === "image" ? "image" : "file"}`}
            href={url}
            target="_blank"
            rel="noreferrer"
            title={`打开 ${item.name}`}
          >
            {item.kind === "image" ? <img src={url} alt={item.name} /> : attachmentIcon(item)}
            <span><strong>{item.name}</strong><small>{attachmentDetail(item)}</small></span>
          </a>
        );
      })}
    </div>
  );
}

export default function AgentPage() {
  const navigate = useNavigate();
  const [messageApi, messageContext] = message.useMessage();
  const [modalApi, modalContext] = Modal.useModal();
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [sessionView, setSessionView] = useState<"active" | "archived">("active");
  const [renameTarget, setRenameTarget] = useState<AgentSession | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [sessionActionLoading, setSessionActionLoading] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<AgentSession | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [models, setModels] = useState<AgentModel[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [branchingMessageId, setBranchingMessageId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [draftAttachments, setDraftAttachments] = useState<DraftAttachment[]>([]);
  const [attachmentLimits, setAttachmentLimits] = useState(DEFAULT_ATTACHMENT_LIMITS);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [skillCenterOpen, setSkillCenterOpen] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pollToken = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const discardDrafts = useCallback((items: DraftAttachment[]) => {
    setDraftAttachments([]);
    items.forEach((item) => {
      if (item.token) void deleteAgentAttachmentDraft(item.sessionId, item.token).catch(() => undefined);
    });
  }, []);

  const refreshSessions = useCallback(async (
    preferredId?: string,
    view: "active" | "archived" = sessionView,
  ) => {
    const items = await listAgentSessions(view === "archived");
    setSessions(items);
    const nextId = preferredId && items.some((item) => item.id === preferredId)
      ? preferredId
      : items[0]?.id || null;
    setActiveId(nextId);
    setActiveSession(nextId ? await getAgentSession(nextId) : null);
    return nextId;
  }, [sessionView]);

  const createSession = useCallback(async () => {
    discardDrafts(draftAttachments);
    setError(null);
    const created = await createAgentSession();
    setSessionView("active");
    setRun(null);
    await refreshSessions(created.id, "active");
  }, [discardDrafts, draftAttachments, refreshSessions]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [items, catalog, limits] = await Promise.all([
          listAgentSessions(),
          listAgentModels(),
          fetchAgentAttachmentLimits().catch(() => DEFAULT_ATTACHMENT_LIMITS),
        ]);
        if (cancelled) return;
        setModels(catalog.items);
        setSelectedModel(catalog.active);
        setAttachmentLimits(limits);
        if (!items.length) {
          const created = await createAgentSession();
          if (cancelled) return;
          setSessions([created]);
          setActiveId(created.id);
          setActiveSession(created);
        } else {
          setSessions(items);
          setActiveId(items[0].id);
          const session = await getAgentSession(items[0].id);
          setActiveSession(session);
          setSelectedModel(session.model_id || catalog.active);
        }
      } catch (cause) {
        if (!cancelled) setError(errorMessage(cause, "无法读取 Agent 会话"));
      } finally {
        if (!cancelled) setLoadingSessions(false);
      }
    })();
    return () => {
      cancelled = true;
      pollToken.current += 1;
    };
  }, []);

  const chooseSession = async (id: string) => {
    discardDrafts(draftAttachments);
    pollToken.current += 1;
    setActiveId(id);
    setRun(null);
    setError(null);
    try {
      const session = await getAgentSession(id);
      setActiveSession(session);
      if (session.model_id) setSelectedModel(session.model_id);
    } catch (cause) {
      setError(errorMessage(cause, "无法打开会话"));
    }
  };

  const changeSessionView = async (view: "active" | "archived") => {
    discardDrafts(draftAttachments);
    pollToken.current += 1;
    setSessionView(view);
    setRun(null);
    setError(null);
    setLoadingSessions(true);
    try {
      await refreshSessions(undefined, view);
    } catch (cause) {
      setError(errorMessage(cause, "无法读取会话列表"));
    } finally {
      setLoadingSessions(false);
    }
  };

  const updateDraft = (localId: string, patch: Partial<DraftAttachment>) => {
    setDraftAttachments((current) => current.map((item) =>
      item.localId === localId ? { ...item, ...patch } : item,
    ));
  };

  const uploadDraft = async (draft: DraftAttachment) => {
    updateDraft(draft.localId, { status: "uploading", progress: 0, error: undefined });
    try {
      const uploaded = await uploadAgentAttachment(
        draft.sessionId,
        draft.file,
        (progress) => updateDraft(draft.localId, { progress }),
      );
      updateDraft(draft.localId, {
        token: uploaded.token,
        attachment: uploaded.attachment,
        status: "ready",
        progress: 100,
      });
    } catch (cause) {
      updateDraft(draft.localId, {
        status: "error",
        error: errorMessage(cause, "附件上传失败"),
      });
    }
  };

  const addFiles = (files: File[]) => {
    if (!activeId || activeSession?.archived || run?.status === "running") return;
    const model = models.find((item) => item.id === selectedModel);
    const additions: DraftAttachment[] = [];
    let documentCount = draftAttachments.filter((item) => isDocumentFile(item.file)).length;
    for (const file of files) {
      const ext = extension(file.name);
      if (!IMAGE_EXTENSIONS.has(ext) && !DOCUMENT_EXTENSIONS.has(ext)) {
        messageApi.warning(`${file.name}：仅支持图片、PDF、Word 和 Excel`);
        continue;
      }
      if (draftAttachments.length + additions.length >= attachmentLimits.max_attachments) {
        messageApi.warning(`每条消息最多 ${attachmentLimits.max_attachments} 个附件`);
        break;
      }
      if (isDocumentFile(file) && documentCount >= attachmentLimits.max_documents) {
        messageApi.warning(`PDF、Word 与 Excel 合计最多 ${attachmentLimits.max_documents} 个`);
        continue;
      }
      const sizeLimit = isImageFile(file) ? attachmentLimits.image_max_bytes : attachmentLimits.document_max_bytes;
      if (file.size > sizeLimit) {
        messageApi.warning(`${file.name}：超过 ${formatBytes(sizeLimit)} 限制`);
        continue;
      }
      if (isImageFile(file) && !model?.supports_images) {
        messageApi.warning("当前模型未启用图片理解，请先切换支持图片的模型");
        continue;
      }
      if (isDocumentFile(file)) documentCount += 1;
      additions.push({
        localId: `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        sessionId: activeId,
        file,
        status: "uploading",
        progress: 0,
      });
    }
    if (!additions.length) return;
    setDraftAttachments((current) => [...current, ...additions]);
    additions.forEach((item) => void uploadDraft(item));
  };

  const removeDraft = async (item: DraftAttachment) => {
    setDraftAttachments((current) => current.filter((draft) => draft.localId !== item.localId));
    if (item.token) await deleteAgentAttachmentDraft(item.sessionId, item.token).catch(() => undefined);
  };

  const startRename = (item: AgentSession) => {
    setRenameTarget(item);
    setRenameValue(item.title);
  };

  const submitRename = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    setSessionActionLoading(true);
    try {
      await renameAgentSession(renameTarget.id, renameValue.trim());
      await refreshSessions(renameTarget.id);
      setRenameTarget(null);
      messageApi.success("会话已重命名");
    } catch (cause) {
      setError(errorMessage(cause, "无法重命名会话"));
    } finally {
      setSessionActionLoading(false);
    }
  };

  const togglePin = async (item: AgentSession) => {
    try {
      await pinAgentSession(item.id, !item.pinned);
      await refreshSessions(item.id);
      messageApi.success(item.pinned ? "已取消置顶" : "会话已置顶");
    } catch (cause) {
      setError(errorMessage(cause, "无法更新置顶状态"));
    }
  };

  const toggleArchive = async (item: AgentSession) => {
    try {
      await archiveAgentSession(item.id, !item.archived);
      await refreshSessions();
      messageApi.success(item.archived ? "会话已恢复" : "会话已归档");
    } catch (cause) {
      setError(errorMessage(cause, "无法更新归档状态"));
    }
  };

  const confirmDelete = (item: AgentSession) => {
    modalApi.confirm({
      title: "永久删除会话？",
      content: `“${item.title}”及其本地消息记录将被删除，此操作无法撤销。`,
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      async onOk() {
        await deleteAgentSession(item.id);
        await refreshSessions();
        messageApi.success("会话已删除");
      },
    });
  };

  const pollRun = async (runId: string, token: number) => {
    try {
      const current = await getAgentRun(runId);
      if (token !== pollToken.current) return;
      setRun(current);
      if (current.status === "running") {
        window.setTimeout(() => pollRun(runId, token), 600);
        return;
      }
      if (activeId) {
        setActiveSession(await getAgentSession(activeId));
        await refreshSessions(activeId);
      }
      if (current.status === "failed") setError(current.error?.message || "Agent 运行失败");
    } catch (cause) {
      if (token === pollToken.current) setError(errorMessage(cause, "无法读取 Agent 运行状态"));
    }
  };

  const send = async (text: string) => {
    const question = text.trim();
    const readyDrafts = draftAttachments.filter((item) => item.status === "ready" && item.token && item.attachment);
    if ((!question && !readyDrafts.length) || !activeId || activeSession?.archived || !selectedModel || run?.status === "running") return;
    if (draftAttachments.some((item) => item.status === "uploading")) {
      setError("请等待附件上传完成后再发送");
      return;
    }
    if (draftAttachments.some((item) => item.status === "error")) {
      setError("请重试或移除上传失败的附件");
      return;
    }
    const model = models.find((item) => item.id === selectedModel);
    if (!model?.available) {
      setError("所选模型尚未配置凭据");
      return;
    }
    if (readyDrafts.some((item) => item.attachment?.kind === "image") && !model.supports_images) {
      setError("当前模型未启用图片理解，请切换支持图片的模型");
      return;
    }
    setError(null);
    const optimistic: AgentMessage = {
      id: `optimistic-${Date.now()}`,
      run_id: "pending",
      role: "user",
      content: question,
      attachments: readyDrafts.map((item) => item.attachment as AgentAttachment),
      model,
      created_at: new Date().toISOString(),
    };
    setActiveSession((current) => current ? {
      ...current,
      messages: [...(current.messages || []), optimistic],
    } : current);
    try {
      const started = await sendAgentMessage(
        activeId,
        question,
        selectedModel,
        readyDrafts.map((item) => item.token as string),
      );
      setInput("");
      setDraftAttachments([]);
      setRun(started);
      const token = ++pollToken.current;
      void pollRun(started.id, token);
    } catch (cause) {
      setError(errorMessage(cause, "Agent 调用失败"));
      setActiveSession(await getAgentSession(activeId));
    }
  };

  const cancel = async () => {
    if (!run || run.status !== "running") return;
    await cancelAgentRun(run.id);
  };

  const copyAnswer = async (item: AgentMessage) => {
    try {
      await navigator.clipboard.writeText(item.content);
      messageApi.success("已复制回答");
    } catch {
      setError("无法访问剪贴板");
    }
  };

  const setFeedback = async (item: AgentMessage, value: "up" | "down") => {
    if (!activeId) return;
    const next = item.feedback === value ? null : value;
    try {
      const updated = await setAgentMessageFeedback(activeId, item.id, next);
      setActiveSession((current) => current ? {
        ...current,
        messages: (current.messages || []).map((messageItem) =>
          messageItem.id === updated.id ? updated : messageItem),
      } : current);
    } catch (cause) {
      setError(errorMessage(cause, "无法保存反馈"));
    }
  };

  const branchFrom = async (item: AgentMessage) => {
    if (!activeId || branchingMessageId) return;
    setBranchingMessageId(item.id);
    setError(null);
    try {
      const branch = await branchAgentSession(activeId, item.id);
      pollToken.current += 1;
      setRun(null);
      setActiveId(branch.id);
      setActiveSession(branch);
      if (branch.model_id) setSelectedModel(branch.model_id);
      await refreshSessions(branch.id);
      messageApi.success("已创建分枝会话");
    } catch (cause) {
      setError(errorMessage(cause, "无法创建分枝会话"));
    } finally {
      setBranchingMessageId(null);
    }
  };

  const messages = activeSession?.messages || [];
  const bubbleItems = messages.map((item) => ({
    key: item.id,
    role: item.role,
    content: item.error ? (
      <Alert type="error" showIcon message={item.error.message} />
    ) : item.role === "assistant" ? <AgentMessageContent message={item} /> : (
      <div className="agent-user-message">
        {item.content && <div>{item.content}</div>}
        <MessageAttachments sessionId={activeId || activeSession?.id || ""} items={item.attachments || []} />
      </div>
    ),
    footer: item.role === "assistant" ? (
      <div className="agent-answer-footer">
        <Space size={10} wrap>
          {item.duration_ms != null && <span>{formatDuration(item.duration_ms)}</span>}
          {item.usage && (
            <Tooltip title={`输入 ${item.usage.input_tokens.toLocaleString()} · 输出 ${item.usage.output_tokens.toLocaleString()}`}>
              <span>{item.usage.total_tokens.toLocaleString()} Token</span>
            </Tooltip>
          )}
        </Space>
        <Space size={2}>
          <Tooltip title="复制回答">
            <Button type="text" size="small" icon={<CopyOutlined />} disabled={!item.content} onClick={() => void copyAnswer(item)} aria-label="复制回答" />
          </Tooltip>
          <Tooltip title={item.feedback === "up" ? "取消点赞" : "点赞"}>
            <Button type="text" size="small" icon={item.feedback === "up" ? <LikeFilled /> : <LikeOutlined />} onClick={() => void setFeedback(item, "up")} aria-label="点赞" aria-pressed={item.feedback === "up"} />
          </Tooltip>
          <Tooltip title={item.feedback === "down" ? "取消点踩" : "点踩"}>
            <Button type="text" size="small" icon={item.feedback === "down" ? <DislikeFilled /> : <DislikeOutlined />} onClick={() => void setFeedback(item, "down")} aria-label="点踩" aria-pressed={item.feedback === "down"} />
          </Tooltip>
          <Tooltip title="在新对话中分枝">
            <Button type="text" size="small" icon={<BranchesOutlined />} loading={branchingMessageId === item.id} onClick={() => void branchFrom(item)} aria-label="在新对话中分枝" />
          </Tooltip>
        </Space>
      </div>
    ) : undefined,
  }));

  const eventItems = (run?.events || []).filter((item) => item.type !== "harness.event" || item.event?.type !== "final");
  const lastMessage = messages.length ? messages[messages.length - 1] : undefined;
  const mode = run?.execution_mode || lastMessage?.execution_mode || "general";
  const configuredSkills = activeSession?.skill_mode === "manual"
    ? activeSession.selected_skills || []
    : [];
  const skillLabel = run?.skill || configuredSkills.join("、");
  const skillActive = mode === "skill" || configuredSkills.length > 0;
  return (
    <XProvider>
      <>
        {messageContext}
        {modalContext}
        <div className="agent-workspace">
        <aside className="agent-sessions">
          <div className="agent-panel-header">
            <Text strong>会话</Text>
            <Tooltip title="新建会话">
              <Button type="text" icon={<PlusOutlined />} onClick={() => void createSession()} />
            </Tooltip>
          </div>
          <Segmented
            className="agent-session-filter"
            block
            size="small"
            value={sessionView}
            onChange={(value) => void changeSessionView(value as "active" | "archived")}
            options={[
              { label: "会话", value: "active" },
              { label: "归档", value: "archived", icon: <InboxOutlined /> },
            ]}
          />
          {loadingSessions ? <Spin className="agent-center" /> : (
            <List
              dataSource={sessions}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={sessionView === "archived" ? "暂无归档会话" : "暂无会话"} /> }}
              renderItem={(item) => (
                <List.Item
                  className={item.id === activeId ? "agent-session active" : "agent-session"}
                  onClick={() => void chooseSession(item.id)}
                >
                  <div className="agent-session-content">
                    <div className="agent-session-title">
                      {item.pinned && <PushpinFilled />}
                      <Text ellipsis>{item.title}</Text>
                    </div>
                    <small>{item.message_count} 条消息</small>
                  </div>
                  <Dropdown
                    trigger={["click"]}
                    menu={{
                      items: [
                        ...(!item.archived ? [{ key: "pin", icon: item.pinned ? <PushpinOutlined /> : <PushpinFilled />, label: item.pinned ? "取消置顶" : "置顶" }] : []),
                        { key: "rename", icon: <EditOutlined />, label: "重命名" },
                        { key: "archive", icon: item.archived ? <UndoOutlined /> : <InboxOutlined />, label: item.archived ? "恢复" : "归档" },
                        { type: "divider" as const },
                        { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true },
                      ],
                      onClick: ({ key, domEvent }) => {
                        domEvent.stopPropagation();
                        if (key === "pin") void togglePin(item);
                        if (key === "rename") startRename(item);
                        if (key === "archive") void toggleArchive(item);
                        if (key === "delete") confirmDelete(item);
                      },
                    }}
                  >
                    <Button
                      className="agent-session-menu"
                      type="text"
                      size="small"
                      icon={<MoreOutlined />}
                      aria-label="会话操作"
                      onClick={(event) => event.stopPropagation()}
                    />
                  </Dropdown>
                </List.Item>
              )}
            />
          )}
        </aside>

        <main
          className={`agent-conversation${draggingFiles ? " is-dragging" : ""}`}
          onDragEnter={(event) => {
            if (event.dataTransfer.types.includes("Files")) {
              event.preventDefault();
              setDraggingFiles(true);
            }
          }}
          onDragOver={(event) => {
            if (event.dataTransfer.types.includes("Files")) event.preventDefault();
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDraggingFiles(false);
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDraggingFiles(false);
            addFiles(Array.from(event.dataTransfer.files));
          }}
        >
          <div className="agent-panel-header">
            <Space size={8} wrap>
              <Text strong>Agent 工作台</Text>
              <Tag color={skillActive ? "green" : "blue"}>
                {skillActive ? `Skill${skillLabel ? ` · ${skillLabel}` : ""}` : "通用 Agent"}
              </Tag>
              {run?.status === "running" && <Tag icon={<SyncOutlined spin />} color="processing">运行中</Tag>}
            </Space>
            <Space size={4}>
              <Tooltip title="生成、管理并为当前会话加载 Skill">
                <Button icon={<ToolOutlined />} onClick={() => setSkillCenterOpen(true)}>Skills</Button>
              </Tooltip>
              <Tooltip title="在独立页面查看完整结构与本次查询路径">
                <Button icon={<FullscreenOutlined />} onClick={() => navigate(`/agent/topology${activeId ? `?session=${encodeURIComponent(activeId)}` : ""}`)}>智慧树</Button>
              </Tooltip>
              {run?.status === "running" && (
                <Tooltip title="取消运行"><Button danger type="text" icon={<StopOutlined />} onClick={() => void cancel()} /></Tooltip>
              )}
            </Space>
          </div>
          <div className="agent-messages">
            {!messages.length && (
              <div className="agent-empty">
                <RobotOutlined />
                <Space direction="vertical" size={8}>
                  {SUGGESTIONS.map((item) => <Button key={item} disabled={activeSession?.archived} onClick={() => void send(item)}>{item}</Button>)}
                </Space>
              </div>
            )}
            <Bubble.List
              items={bubbleItems}
              roles={{
                assistant: {
                  placement: "start",
                  avatar: { icon: <RobotOutlined />, style: { background: "#1769aa" } },
                  style: { width: "min(880px, 100%)", maxWidth: 880, whiteSpace: "normal" },
                },
                user: {
                  placement: "end",
                  avatar: { icon: <UserOutlined />, style: { background: "#16794b" } },
                  style: { maxWidth: 720, whiteSpace: "pre-wrap" },
                },
              }}
            />
            {run?.status === "running" && <div className="agent-running"><Spin size="small" /> Agent 正在处理</div>}
            {error && <Alert type="error" showIcon closable message={error} onClose={() => setError(null)} />}
          </div>
          <div className="agent-sender">
            {draftAttachments.length > 0 && (
              <div className="agent-draft-attachments">
                {draftAttachments.map((item) => {
                  const record = item.attachment;
                  const previewUrl = record && item.token
                    ? agentAttachmentContentUrl(item.sessionId, record.id, item.token)
                    : "";
                  return (
                    <div key={item.localId} className={`agent-draft-attachment ${item.status}`}>
                      {record?.kind === "image" && previewUrl
                        ? <img src={previewUrl} alt={record.name} />
                        : <span className="agent-draft-icon">{record ? attachmentIcon(record) : isImageFile(item.file) ? <FileImageOutlined /> : isDocumentFile(item.file) && ["xls", "xlsx"].includes(extension(item.file.name)) ? <FileExcelOutlined /> : extension(item.file.name) === "pdf" ? <FilePdfOutlined /> : <FileWordOutlined />}</span>}
                      <span className="agent-draft-meta">
                        <strong title={item.file.name}>{item.file.name}</strong>
                        <small>
                          {item.status === "uploading" && `上传中 ${item.progress}%`}
                          {item.status === "ready" && record && attachmentDetail(record)}
                          {item.status === "error" && (item.error || "上传失败")}
                        </small>
                      </span>
                      {item.status === "error" && (
                        <Tooltip title="重试">
                          <Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => void uploadDraft(item)} aria-label={`重试 ${item.file.name}`} />
                        </Tooltip>
                      )}
                      <Tooltip title="移除">
                        <Button type="text" size="small" icon={<CloseOutlined />} onClick={() => void removeDraft(item)} aria-label={`移除 ${item.file.name}`} />
                      </Tooltip>
                    </div>
                  );
                })}
              </div>
            )}
            <input
              ref={fileInputRef}
              className="agent-file-input"
              type="file"
              accept={ACCEPTED_ATTACHMENTS}
              multiple
              onChange={(event) => {
                addFiles(Array.from(event.target.files || []));
                event.target.value = "";
              }}
            />
            <Sender
              value={input}
              loading={run?.status === "running"}
              disabled={!activeSession || Boolean(activeSession.archived)}
              onChange={setInput}
              onSubmit={send}
              onPasteFile={(_firstFile, files) => addFiles(Array.from(files))}
              placeholder={activeSession?.archived ? "归档会话只读" : "输入任务或问题"}
              actions={() => (
                <Space size={4} align="center" wrap={false}>
                  <Tooltip title="添加图片、PDF、Word 或 Excel">
                    <Button
                      type="text"
                      icon={<PaperClipOutlined />}
                      disabled={!activeSession || Boolean(activeSession.archived) || run?.status === "running"}
                      onClick={() => fileInputRef.current?.click()}
                      aria-label="添加附件"
                    />
                  </Tooltip>
                  <Select
                    className="agent-inline-model-select"
                    size="small"
                    variant="borderless"
                    value={selectedModel || undefined}
                    disabled={run?.status === "running" || Boolean(activeSession?.archived)}
                    loading={!models.length}
                    onChange={setSelectedModel}
                    popupMatchSelectWidth={260}
                    options={models.map((item) => ({
                      value: item.id,
                      disabled: !item.available,
                      label: item.name.replace(" Responses", ""),
                      title: `${item.name} · ${item.model}${item.available ? "" : "（未配置）"}`,
                    }))}
                    optionRender={(option) => {
                      const item = models.find((model) => model.id === option.value);
                      return item ? (
                        <div className="agent-model-option">
                          <span>{item.name}</span>
                          <small>{item.model}{item.available ? "" : " · 未配置"}</small>
                        </div>
                      ) : option.label;
                    }}
                    aria-label="选择 Agent 模型"
                  />
                  <Tooltip title="发送">
                    <Button
                      type="primary"
                      shape="circle"
                      icon={<ArrowUpOutlined />}
                      loading={run?.status === "running"}
                      disabled={
                        !activeSession
                        || Boolean(activeSession.archived)
                        || draftAttachments.some((item) => item.status === "uploading")
                        || (!input.trim() && !draftAttachments.some((item) => item.status === "ready"))
                      }
                      onClick={() => void send(input)}
                      aria-label="发送消息"
                    />
                  </Tooltip>
                </Space>
              )}
            />
            {draggingFiles && <div className="agent-drop-overlay"><PaperClipOutlined />松开以添加附件</div>}
          </div>
        </main>

        <aside className="agent-inspector">
          <div className="agent-panel-header"><Text strong><ToolOutlined /> 执行轨迹</Text></div>
          {eventItems.length ? (
            <Timeline
              items={eventItems.map((item) => ({
                color: timelineColor(item),
                children: <div><Text>{eventText(item)}</Text><small>{new Date(item.created_at).toLocaleTimeString()}</small></div>,
              }))}
            />
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无运行" />}
        </aside>
        </div>
        <Modal
          title="重命名会话"
          open={Boolean(renameTarget)}
          okText="保存"
          cancelText="取消"
          confirmLoading={sessionActionLoading}
          okButtonProps={{ disabled: !renameValue.trim() }}
          onOk={() => void submitRename()}
          onCancel={() => setRenameTarget(null)}
          destroyOnHidden
        >
          <Input
            value={renameValue}
            maxLength={80}
            autoFocus
            onChange={(event) => setRenameValue(event.target.value)}
            onPressEnter={() => void submitRename()}
          />
        </Modal>
        <SkillCenter
          open={skillCenterOpen}
          session={activeSession}
          modelId={selectedModel}
          running={run?.status === "running"}
          onClose={() => setSkillCenterOpen(false)}
          onSessionChange={(updated) => {
            setActiveSession(updated);
            setSessions((current) => current.map((item) => item.id === updated.id ? updated : item));
          }}
        />
      </>
    </XProvider>
  );
}
