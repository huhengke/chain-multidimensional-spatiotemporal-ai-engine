import { http } from "./client";
import type {
  AggregationsResponse,
  AggregateScope,
  AgentModel,
  AgentAttachmentLimits,
  AgentAttachmentUpload,
  AgentMessage,
  AgentRun,
  AgentSession,
  AgentSkillDetail,
  AgentSkillDraft,
  AgentSkillDraftResponse,
  AgentSkillMode,
  AgentSkillSummary,
  AgentSkillValidation,
  ChatResponse,
  DatasetRowsResponse,
  DatasetSummary,
  DatasourceTable,
  DatasourceTablePreview,
  ImportTaskInfo,
  LevelValuesResponse,
  ProbeResult,
  RecordsResponse,
  ReinitializePreview,
  ReinitializeReadiness,
  ReinitializeResult,
  ModelConfig,
  SpiralTopologySummary,
  SpiralTopologySliceResponse,
  SystemParamsResponse,
  SystemParamsUpdateResponse,
  SpiralTreeResponse,
  TokenResponse,
  UserInfo,
  FoundationConfig,
  FoundationSuggestion,
  RuleSuggestion,
  RuntimeAuditEvent,
  RuntimeProfile,
  RuntimeProfilesResponse,
} from "./types";

// ---------------- 认证 ----------------

export const login = (username: string, password: string) =>
  http
    .post<TokenResponse>("/api/v1/auth/login", { username, password })
    .then((r) => r.data);

export const fetchMe = () =>
  http.get<UserInfo>("/api/v1/auth/me").then((r) => r.data);

// ---------------- Agent 对话 ----------------

export const chat = (message: string) =>
  http
    .post<ChatResponse>("/api/v1/chat", { message })
    .then((r) => r.data);

export const createAgentSession = (title = "新会话") =>
  http
    .post<AgentSession>("/api/v1/agent/sessions", { title })
    .then((r) => r.data);

export const listAgentSessions = (archived = false) =>
  http
    .get<{ items: AgentSession[] }>("/api/v1/agent/sessions", { params: { archived } })
    .then((r) => r.data.items);

export const renameAgentSession = (sessionId: string, title: string) =>
  http
    .post<AgentSession>(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/rename`, { title })
    .then((r) => r.data);

export const pinAgentSession = (sessionId: string, pinned: boolean) =>
  http
    .post<AgentSession>(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/pin`, { pinned })
    .then((r) => r.data);

export const archiveAgentSession = (sessionId: string, archived: boolean) =>
  http
    .post<AgentSession>(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/archive`, { archived })
    .then((r) => r.data);

export const deleteAgentSession = (sessionId: string) =>
  http
    .delete<{ deleted: boolean }>(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}`)
    .then((r) => r.data);

export const getAgentSession = (sessionId: string) =>
  http
    .get<AgentSession>(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}`)
    .then((r) => r.data);

export const listAgentModels = () =>
  http
    .get<{ active: string; items: AgentModel[] }>("/api/v1/agent/models")
    .then((r) => r.data);

export const listAgentSkills = () =>
  http
    .get<{ items: AgentSkillSummary[] }>("/api/v1/agent/skills")
    .then((r) => r.data.items);

export const getAgentSkill = (name: string) =>
  http
    .get<AgentSkillDetail>(`/api/v1/agent/skills/${encodeURIComponent(name)}`)
    .then((r) => r.data);

export const analyzeAgentSkill = (
  idea: string,
  model: string,
  sessionId?: string,
  includeContext = true,
) =>
  http
    .post<AgentSkillDraftResponse>("/api/v1/agent/skills/analyze", {
      idea,
      model,
      session_id: sessionId,
      include_context: includeContext,
    }, { timeout: 3 * 60 * 1000 })
    .then((r) => r.data);

export const validateAgentSkill = (draft: AgentSkillDraft) =>
  http
    .post<AgentSkillValidation>("/api/v1/agent/skills/validate", draft)
    .then((r) => r.data);

export const publishAgentSkill = (
  draft: AgentSkillDraft & { confirmed: true; acknowledge_warnings: boolean },
) =>
  http
    .post<AgentSkillDetail>("/api/v1/agent/skills/publish", draft)
    .then((r) => r.data);

export const setAgentSkillStatus = (name: string, enabled: boolean) =>
  http
    .post<AgentSkillDetail>(`/api/v1/agent/skills/${encodeURIComponent(name)}/status`, { enabled })
    .then((r) => r.data);

export const archiveAgentSkill = (name: string) =>
  http
    .delete<{ archived: boolean; name: string }>(`/api/v1/agent/skills/${encodeURIComponent(name)}`)
    .then((r) => r.data);

export const updateAgentSessionSkills = (
  sessionId: string,
  mode: AgentSkillMode,
  skills: string[],
) =>
  http
    .post<AgentSession>(`/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/skills`, {
      mode,
      skills,
    })
    .then((r) => r.data);

export const fetchAgentAttachmentLimits = () =>
  http
    .get<AgentAttachmentLimits>("/api/v1/agent/attachments/limits")
    .then((r) => r.data);

export const uploadAgentAttachment = (
  sessionId: string,
  file: File,
  onProgress?: (percent: number) => void,
) => {
  const form = new FormData();
  form.append("file", file);
  return http
    .post<AgentAttachmentUpload>(
      `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/attachments`,
      form,
      {
        timeout: 3 * 60 * 1000,
        onUploadProgress: (event) => {
          if (event.total) onProgress?.(Math.round((event.loaded / event.total) * 100));
        },
      },
    )
    .then((r) => r.data);
};

export const deleteAgentAttachmentDraft = (sessionId: string, token: string) =>
  http
    .delete<{ deleted: boolean }>(
      `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(token)}`,
    )
    .then((r) => r.data);

export const agentAttachmentContentUrl = (
  sessionId: string,
  attachmentId: string,
  token?: string,
) => `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}/content${token ? `?token=${encodeURIComponent(token)}` : ""}`;

export const sendAgentMessage = (
  sessionId: string,
  message: string,
  model: string,
  attachments: string[] = [],
) =>
  http
    .post<AgentRun>(
      `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/messages`,
      { message, model, attachments },
    )
    .then((r) => r.data);

export const setAgentMessageFeedback = (
  sessionId: string,
  messageId: string,
  feedback: "up" | "down" | null,
) =>
  http
    .post<AgentMessage>(
      `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/feedback`,
      { feedback },
    )
    .then((r) => r.data);

export const branchAgentSession = (sessionId: string, messageId: string) =>
  http
    .post<AgentSession>(
      `/api/v1/agent/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/branch`,
    )
    .then((r) => r.data);

export const getAgentRun = (runId: string) =>
  http
    .get<AgentRun>(`/api/v1/agent/runs/${encodeURIComponent(runId)}`)
    .then((r) => r.data);

export const cancelAgentRun = (runId: string) =>
  http
    .post<{ cancelled: boolean }>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/cancel`)
    .then((r) => r.data);

// ---------------- 引擎查询 ----------------

export const queryAggregations = (
  dataset: string,
  conditions: Record<string, string>,
  groupBy: string | null = null
) =>
  http
    .post<AggregationsResponse>("/api/v1/engine/aggregations/query", {
      dataset,
      conditions,
      group_by: groupBy,
    })
    .then((r) => r.data);

export const queryRecords = (
  dataset: string,
  filters: Record<string, string>,
  page = 1,
  pageSize = 20
) =>
  http
    .post<RecordsResponse>("/api/v1/engine/records/query", {
      dataset,
      filters,
      page,
      page_size: pageSize,
    })
    .then((r) => r.data);

// ---------------- 导入 ----------------

export const startTestImport = (dataset: string, mode: string) => {
  const form = new URLSearchParams({ dataset, mode });
  return http
    .post<{ task_uuid: string }>("/api/v1/engine/import/test-data", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    })
    .then((r) => r.data);
};

export const fetchTask = (taskUuid: string) =>
  http
    .get<ImportTaskInfo>(`/api/v1/engine/import/tasks/${taskUuid}`)
    .then((r) => r.data);

export const listTasks = () =>
  http
    .get<{ items: ImportTaskInfo[] }>("/api/v1/engine/import/tasks")
    .then((r) => r.data.items);

// ---------------- 数据集注册 ----------------

export const listDatasets = () =>
  http
    .get<{ items: DatasetSummary[]; total: number }>("/api/v1/engine/datasets")
    .then((r) => r.data);

export const listDisabledDatasets = () =>
  http
    .get<{ items: DatasetSummary[]; total: number }>("/api/v1/engine/datasets/disabled")
    .then((r) => r.data);

export const getDataset = (dataset: string) =>
  http.get<DatasetSummary>(`/api/v1/engine/datasets/${dataset}`).then((r) => r.data);

/** 上传 Excel 探测表头与列画像（注册向导第一步） */
export const probeDataset = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return http
    .post<ProbeResult>("/api/v1/engine/datasets/probe", form)
    .then((r) => r.data);
};

/** 注册/覆盖自定义数据集（草案 → 声明） */
export const upsertDataset = (dataset: string, draft: Record<string, unknown>) =>
  http
    .put(`/api/v1/engine/datasets/${dataset}`, draft)
    .then((r) => r.data);

export const deleteDataset = (dataset: string) =>
  http.delete(`/api/v1/engine/datasets/${dataset}`).then((r) => r.data);

export const restoreDataset = (dataset: string) =>
  http.post(`/api/v1/engine/datasets/${dataset}/restore`).then((r) => r.data);

export const rebuildDataset = (
  dataset: string,
  scope: AggregateScope = "all",
  nodeIds: string[] = [],
) =>
  http
    .post(`/api/v1/engine/datasets/${dataset}/rebuild`, {
      scope,
      node_ids: nodeIds,
    }, {
      // 全量重算会扫描事实表并重建拓扑；大数据集可能超过统一请求超时。
      timeout: 10 * 60 * 1000,
    })
    .then((r) => r.data);

/** 按数据集导入：优先用探测阶段落盘的 file_token，避免重复上传 */
export const importDataset = (
  dataset: string,
  mode: string,
  opts: { fileToken?: string; file?: File } = {}
) => {
  const form = new FormData();
  form.append("mode", mode);
  if (opts.file) form.append("file", opts.file);
  else if (opts.fileToken) form.append("file_token", opts.fileToken);
  return http
    .post<{ task_uuid: string }>(`/api/v1/engine/datasets/${dataset}/import`, form)
    .then((r) => r.data);
};

export const listDatasetRows = (
  dataset: string,
  page = 1,
  pageSize = 20
) =>
  http
    .get<DatasetRowsResponse>(`/api/v1/engine/datasets/${dataset}/rows`, {
      params: { page, page_size: pageSize },
    })
    .then((r) => r.data);

/** 层级值检索：数据详情筛选框自动补全（引擎按节点值去重，记录数倒序） */
export const levelValues = (
  dataset: string,
  level: string,
  q = "",
  limit = 20
) =>
  http
    .get<LevelValuesResponse>(
      `/api/v1/engine/datasets/${dataset}/levels/${level}/values`,
      { params: { q, limit } }
    )
    .then((r) => r.data);

export const reloadDatasets = () =>
  http.post("/api/v1/engine/datasets/reload").then((r) => r.data);

// ---------------- AI 分析与安全重初始化 ----------------

export const previewReinitialize = () =>
  http
    .get<ReinitializePreview>("/api/v1/engine/reinitialize/preview")
    .then((r) => r.data);

export const analyzeReinitialize = () =>
  http
    .post<ReinitializePreview>("/api/v1/engine/reinitialize/analyze")
    .then((r) => r.data);

export const readinessReinitialize = () =>
  http
    .get<ReinitializeReadiness>("/api/v1/engine/reinitialize/readiness")
    .then((r) => r.data);

export const executeReinitialize = (confirmation: string, allowMissingSources = false) =>
  http
    .post<ReinitializeResult>("/api/v1/engine/reinitialize/execute", { confirmation, allow_missing_sources: allowMissingSources })
    .then((r) => r.data);

// ---------------- 生长 ----------------

export const grow = (dataset: string) =>
  http
    .post("/api/v1/engine/grow", { dataset })
    .then((r) => r.data);

// ---------------- 规则 ----------------

export const fetchRules = () =>
  http.get("/api/v1/engine/derive-rules").then((r) => r.data);

export const updateRules = (yamlContent: string) =>
  http
    .put("/api/v1/engine/derive-rules", { yaml_content: yamlContent })
    .then((r) => r.data);

export const reloadRules = () =>
  http.post("/api/v1/engine/derive-rules/reload").then((r) => r.data);

export const analyzeRules = (body: { idea: string; rules?: Record<string, unknown>[] }) =>
  http
    .post<{ backend: string; suggestions: RuleSuggestion[]; message: string }>(
      "/api/v1/engine/derive-rules/analyze",
      body,
    )
    .then((r) => r.data);

// ---------------- 数据源 ----------------

export const listDatasources = () =>
  http.get("/api/v1/engine/datasources").then((r) => r.data);

export const testDatasource = (config: Record<string, unknown>) =>
  http
    .post("/api/v1/engine/datasources/test", { config })
    .then((r) => r.data);

export const listDatasourceTables = (sourceId: string) =>
  http
    .get<{ source_id: string; items: DatasourceTable[] }>(
      `/api/v1/engine/datasources/${encodeURIComponent(sourceId)}/tables`
    )
    .then((r) => r.data);

export const previewDatasourceTable = (sourceId: string, table: string) =>
  http
    .get<DatasourceTablePreview>(
      `/api/v1/engine/datasources/${encodeURIComponent(sourceId)}/tables/${encodeURIComponent(table)}/preview`
    )
    .then((r) => r.data);

export const probeDatabaseDataset = (datasourceId: string, table: string) => {
  const form = new FormData();
  form.append("datasource_id", datasourceId);
  form.append("table", table);
  return http
    .post<import("./types").ProbeResult>("/api/v1/engine/datasets/probe-database", form)
    .then((r) => r.data);
};

export const fetchModelConfig = () =>
  http.get<ModelConfig>("/api/v1/engine/model-config").then((r) => r.data);

export const updateModelConfig = (body: Record<string, unknown>) =>
  http.put<ModelConfig>("/api/v1/engine/model-config", body).then((r) => r.data);

export const fetchDatasetContract = (dataset: string) =>
  http.get(`/api/v1/engine/datasets/${encodeURIComponent(dataset)}/contract`).then((r) => r.data);

// ---------------- 基座特征配置 ----------------

export const fetchFoundationFeatures = () =>
  http.get<FoundationConfig>("/api/v1/engine/foundation-features").then((r) => r.data);

export const updateFoundationFeatures = (body: FoundationConfig) =>
  http.put<{ config: FoundationConfig; message: string }>("/api/v1/engine/foundation-features", body).then((r) => r.data);

export const analyzeFoundationFeatures = (body: { idea: string; datasets: Record<string, unknown>[] }) =>
  http.post<{ backend: string; suggestions: FoundationSuggestion[]; config: FoundationConfig; message: string }>("/api/v1/engine/foundation-features/analyze", body).then((r) => r.data);

export const decideFoundationSuggestion = (id: string, approve: boolean) =>
  http.post<{ config: FoundationConfig; message: string }>(`/api/v1/engine/foundation-features/suggestions/${encodeURIComponent(id)}/${approve ? "approve" : "reject"}`).then((r) => r.data);

// ---------------- 物理双链拓扑 ----------------

/** 读取有限深度的真实拓扑切片，避免前端展开整棵螺旋树。 */
export const getSpiralTree = (dataset: string, maxDepth = 2) =>
  http
    .get<SpiralTreeResponse>("/api/v1/engine/spiral/tree", {
      params: { dataset, max_depth: maxDepth },
    })
    .then((r) => r.data);

/** 读取数据集的物理双链统计；DNA 只作为数据链拓扑，不增加查询层级。 */
export const getSpiralTopology = (dataset: string) =>
  http
    .get<SpiralTopologySummary>("/api/v1/engine/spiral/topology", {
      params: { dataset },
    })
    .then((r) => r.data);

export const getSpiralTopologySlice = (dataset: string, limit = 96) =>
  http
    .get<SpiralTopologySliceResponse>("/api/v1/engine/spiral/topology/slice", {
      params: { dataset, limit },
    })
    .then((r) => r.data);

// ---------------- 系统参数 ----------------

export const fetchSystemParams = () =>
  http.get<SystemParamsResponse>("/api/v1/engine/system-params").then((r) => r.data);

export const updateSystemParams = (params: Record<string, number>) =>
  http
    .put<SystemParamsUpdateResponse>("/api/v1/engine/system-params", { params })
    .then((r) => r.data);

// ---------------- AI 数据引擎与数据集运行配置档 ----------------

export const fetchRuntimeProfiles = () =>
  http
    .get<RuntimeProfilesResponse>("/api/v1/engine/runtime-profiles")
    .then((r) => r.data);

export const createRuntimeProfile = (body: Record<string, unknown>) =>
  http
    .post<RuntimeProfile>("/api/v1/engine/runtime-profiles", body)
    .then((r) => r.data);

export const updateRuntimeProfile = (
  profileId: string,
  body: Record<string, unknown>,
) =>
  http
    .put<RuntimeProfile>(
      `/api/v1/engine/runtime-profiles/${encodeURIComponent(profileId)}`,
      body,
    )
    .then((r) => r.data);

export const verifyRuntimeProfile = (
  profileId: string,
  expectedVersion: number,
  reason: string,
) =>
  http
    .post<RuntimeProfile>(
      `/api/v1/engine/runtime-profiles/${encodeURIComponent(profileId)}/verify`,
      { expected_version: expectedVersion, reason },
    )
    .then((r) => r.data);

export const readyRuntimeProfile = (
  profileId: string,
  expectedVersion: number,
  reason: string,
) =>
  http
    .post<RuntimeProfile>(
      `/api/v1/engine/runtime-profiles/${encodeURIComponent(profileId)}/ready`,
      { expected_version: expectedVersion, reason },
    )
    .then((r) => r.data);

export const activateRuntimeProfile = (
  profileId: string,
  expectedRevision: number,
  reason: string,
) =>
  http
    .post<{ active: RuntimeProfile }>(
      `/api/v1/engine/runtime-profiles/${encodeURIComponent(profileId)}/activate`,
      {
        expected_revision: expectedRevision,
        confirm_profile_id: profileId,
        reason,
      },
    )
    .then((r) => r.data);

export const fetchRuntimeProfileAudit = (limit = 200) =>
  http
    .get<{ items: RuntimeAuditEvent[] }>(
      "/api/v1/engine/runtime-profiles/audit",
      { params: { limit } },
    )
    .then((r) => r.data.items);
