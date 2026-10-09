// 与后端契约对齐的类型定义

export interface UserInfo {
  username: string;
  display_name: string;
  role: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserInfo;
}

export interface RouteStep {
  level: number;
  level_name: string;
  level_label: string;
  value: string;
  status: string;
  parent_value?: string;
  message?: string;
  aggregations?: Record<string, string> | null;
  record_count?: number;
  phase?: "foundation" | "middle_inner" | "middle_store" | "outer_feature" | "fact_result" | string;
  temporal_layer?: number | null;
  time_bucket?: string | null;
  branch_depth?: number | null;
}

export interface RouteInfo {
  dataset: string;
  mode: string;
  steps: RouteStep[];
  target_node_ids: string[];
  pruned_node_count: number;
  pruned_record_count: number;
  collapsed_messages: string[];
  query_path?: string[];
  inner_node_ids?: string[];
  middle_store_node_ids?: string[];
  outer_feature_keys?: string[];
  physical_time_buckets?: string[];
  dna_is_topology_not_query_level?: boolean;
}

/**
 * 本次查询涉及的单个数据集路由视图。
 * 批量查询时每个数据集都保留自己的真实路由，即使当前期间没有命中数据，
 * 也要明确展示“已参与查询、当前无数据”，不能回退到某个默认数据集。
 */
export interface DatasetRouteView {
  dataset: string;
  label: string;
  available: boolean;
  route: RouteInfo | null;
  steps: RouteStep[];
  record_count?: number;
  /** true 表示本次主查询实际执行了该数据集；false 表示仅由范围说明补充可用性。 */
  queried?: boolean;
  scope_only?: boolean;
}

export interface NodeBrief {
  node_id: string;
  level: number;
  level_name: string;
  node_value: string;
  record_count: number;
  aggregations: Record<string, string>;
  grown?: boolean;
}

export interface AggregationsResponse {
  dataset: string;
  group_by: string | null;
  nodes: NodeBrief[];
  route: RouteInfo;
}

export interface RecordItem {
  store_id?: string;
  store_name?: string;
  org_code?: string;
  yyyymm?: string;
  gas_code?: string;
  check_mode?: string;
  amount?: string;
  qty_ton?: string;
  qty_liter?: string;
  [key: string]: unknown;
}

export interface RecordsResponse {
  items: RecordItem[];
  total: number;
  page: number;
  page_size: number;
  route: RouteInfo | null;
}

export interface IntentInfo {
  intent: string;
  dataset: string;
  datasets?: string[];
  dataset_scope?: string;
  conditions: Record<string, string>;
  group_by: string | null;
  detail: boolean;
}

export interface ChatResponse {
  answer: string;
  intent: IntentInfo & {
    execution_mode?: "general" | "skill";
    skill?: string | null;
  };
  thought_chain: RouteStep[];
  data: unknown;
  llm_backend: string;
}

export interface AgentModel {
  id: string;
  name: string;
  provider: string;
  protocol: string;
  model: string;
  base_url: string;
  available: boolean;
  active: boolean;
  supports_images: boolean;
}

export interface AgentAttachment {
  id: string;
  kind: "image" | "document" | "workbook";
  document_type: "png" | "jpeg" | "webp" | "gif" | "pdf" | "doc" | "docx" | "xls" | "xlsx" | string;
  name: string;
  mime_type: string;
  size: number;
  sha256: string;
  status: "ready" | string;
  page_count?: number;
  sheet_count?: number;
  non_empty_cells?: number;
  has_extractable_text?: boolean;
  has_vision_pages?: boolean;
}

export interface AgentAttachmentUpload {
  token: string;
  attachment: AgentAttachment;
}

export interface AgentAttachmentLimits {
  max_attachments: number;
  max_documents: number;
  image_max_bytes: number;
  document_max_bytes: number;
  pdf_max_pages: number;
  workbook_max_sheets: number;
  workbook_max_cells: number;
}

export interface AgentUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export type AgentSkillMode = "auto" | "manual";

export interface AgentSkillSummary {
  name: string;
  display_name: string;
  description: string;
  version: number;
  source: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentSkillDetail extends AgentSkillSummary {
  instructions: string;
}

export interface AgentSkillDraft {
  name: string;
  display_name: string;
  description: string;
  instructions: string;
  expected_version?: number;
}

export interface AgentSkillValidation {
  valid: boolean;
  errors: string[];
  warnings: string[];
  normalized?: AgentSkillDraft;
}

export interface AgentSkillDraftResponse {
  status: "pending_review";
  draft: AgentSkillDraft;
  validation: AgentSkillValidation;
  model: Pick<AgentModel, "id" | "name" | "model">;
}

export interface AgentChartSeries {
  name: string;
  data: Array<number | null>;
}

export interface AgentChartArtifact {
  id: string;
  version: number;
  type: "line";
  title: string;
  x_axis: {
    name?: string;
    categories: string[];
  };
  y_axis: {
    name?: string;
    unit?: string;
  };
  series: AgentChartSeries[];
  source: {
    datasets: string[];
    metrics: string[];
    period?: string;
  };
}

export interface AgentMessage {
  id: string;
  run_id: string;
  role: "user" | "assistant";
  content: string;
  execution_mode?: "general" | "skill";
  skill?: string | null;
  model?: Pick<AgentModel, "id" | "name" | "provider" | "model"> | null;
  usage?: AgentUsage | null;
  duration_ms?: number | null;
  dataset_views?: DatasetRouteView[];
  visualizations?: AgentChartArtifact[];
  attachments?: AgentAttachment[];
  feedback?: "up" | "down" | null;
  feedback_updated_at?: string;
  error?: { code: string; message: string } | null;
  created_at: string;
}

export interface AgentSession {
  id: string;
  harness_session_id: string | null;
  model_id: string | null;
  model_name: string | null;
  pinned: boolean;
  archived: boolean;
  skill_mode: AgentSkillMode;
  selected_skills: string[];
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_run_id: string | null;
  source_session_id?: string;
  source_message_id?: string;
  branch_context_pending?: boolean;
  messages?: AgentMessage[];
}

export interface AgentRunEvent {
  id: string;
  run_id: string;
  type: string;
  created_at: string;
  mode?: "general" | "skill";
  message?: string;
  answer?: string;
  error?: { code: string; message: string };
  event?: Record<string, unknown>;
  visualizations?: AgentChartArtifact[];
}

export interface AgentRun {
  id: string;
  session_id: string;
  harness_session_id: string | null;
  status: "running" | "completed" | "failed" | "cancelled";
  execution_mode: "general" | "skill";
  skill: string | null;
  skill_mode: AgentSkillMode;
  requested_skills: string[];
  model: Pick<AgentModel, "id" | "name" | "provider" | "model" | "supports_images">;
  answer: string;
  usage: AgentUsage;
  duration_ms: number | null;
  dataset_views: DatasetRouteView[];
  visualizations: AgentChartArtifact[];
  attachments?: AgentAttachment[];
  error: { code: string; message: string } | null;
  created_at: string;
  completed_at: string | null;
  events: AgentRunEvent[];
}

export interface SpiralTopology {
  version: number;
  role: "base" | "trunk" | "branch" | "node" | string;
  helix_layer: number | null;
  temporal_layer: number | null;
  time_bucket: string | null;
  time_granularity: string | null;
  branch_depth: number | null;
  growth_parent_id: string | null;
  growth_dimension: string | null;
  capacity_snapshot: number | null;
  growth_version: number | null;
  ring_index: number | null;
  strand_a_prev: string | null;
  strand_a_next: string | null;
  strand_b_prev: string | null;
  strand_b_next: string | null;
  pair_node_id: string | null;
  feature_slots: Record<string, unknown>;
}

export interface SpiralTreeNode {
  node_id: string;
  level: number;
  level_name: string;
  value: string;
  record_count: number;
  aggregations: Record<string, string>;
  grown: boolean;
  topology: SpiralTopology;
  children: SpiralTreeNode[];
}

export interface SpiralTreeResponse {
  dataset: string;
  max_depth: number;
  tree: SpiralTreeNode;
}

export interface SpiralTopologySummary {
  dataset: string;
  topology_version: number;
  node_count: number;
  helix_layers: number;
  temporal_layers?: number;
  paired_nodes: number;
  strand_a_edges: number;
  strand_b_edges: number;
  trunk_nodes?: number;
  branch_nodes?: number;
  grown_nodes?: number;
  growth_threshold?: number;
  physical_levels?: string[];
  spatial_layers?: string[];
  valid: boolean;
  errors: string[];
  dna_is_topology_not_query_level: boolean;
  query_path?: string[];
}

export interface SpiralTopologyNode {
  node_id: string;
  dataset: string;
  level: number;
  level_name: string;
  value: string;
  parent_id: string | null;
  path: string;
  record_count: number;
  aggregations: Record<string, string>;
  grown: boolean;
  topology: SpiralTopology;
}

export interface SpiralTopologySliceResponse {
  dataset: string;
  limit: number;
  items: SpiralTopologyNode[];
}

export interface ImportTaskInfo {
  task_uuid: string;
  dataset: string;
  mode: string;
  file_name: string;
  status: string;
  phase: string;
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  skipped: number;
  error_sample: { row: number; reason: string }[];
  message: string | null;
  running?: boolean;
  source_kind?: string;
  source_ref?: Record<string, unknown>;
}

// ---------------- 数据集注册 ----------------

export interface DatasetSummary {
  id: string;
  kind: "generic" | "dimension" | string;
  label: string;
  origin: "base" | "interface" | "catalog";
  source_origin?: "base" | "interface" | "catalog";
  editable: boolean;
  disabled?: boolean;
  can_unregister?: boolean;
  can_restore?: boolean;
  source_file?: string;
  levels?: string[];
  level_labels?: Record<string, string>;
  level_value_key?: Record<string, string>;
  metrics?: { name: string; source?: string; column?: string; label: string }[];
  columns?: { source: string; name: string; label: string }[];
  time?: { source?: string; format?: string };
  base?: { enabled?: boolean; features?: BaseFeature[] };
  source?: { kind?: string; datasource_id?: string; table?: string; [key: string]: unknown };
  structure?: DatasetStructure;
  [key: string]: unknown;
}

export interface DatasetStructureField {
  source: string;
  name: string;
  label: string;
  layers: FeatureLayer[];
  layer_labels: string[];
  role: string;
  semantic_type: BaseSemanticType;
  query_uses: QueryUse[];
  recommended?: boolean;
}

export interface DatasetStructure {
  feature_layer_source: "declared" | "fallback" | "catalog";
  feature_layers: DatasetStructureField[];
  layer_counts: Record<"base" | "trunk" | "branch", number>;
  dna: { layer_count: number; length: number; keys: string[] };
  spiral: {
    layer_count: number;
    length: number;
    levels: Array<{
      index: number;
      name: string;
      label: string;
      source: string;
      layers: FeatureLayer[];
    }>;
  };
  physical_topology?: {
    topology_version: number;
    node_count: number;
    temporal_layers?: number;
    helix_layers: number;
    paired_nodes: number;
    strand_a_edges: number;
    strand_b_edges: number;
    trunk_nodes?: number;
    branch_nodes?: number;
    grown_nodes?: number;
    growth_threshold?: number;
    physical_levels?: string[];
    spatial_layers?: string[];
    valid: boolean;
    errors: string[];
    dna_is_topology_not_query_level: boolean;
    query_path?: string[];
  };
  growth: {
    enabled: boolean;
    configured: boolean;
    stats_available: boolean;
    threshold: number;
    node_count: number;
    grown_node_count: number;
    candidate_node_count: number;
    plan?: {
      source?: string;
      trunk_levels?: string[];
      branch_levels?: string[];
      physical_levels?: string[];
    };
    grown_paths: Array<{
      node_id: string;
      level: number;
      level_name: string;
      level_label: string;
      node_value: string;
      record_count: number;
      path: string;
    }>;
  };
  aggregation: {
    default_scope: AggregateScope;
    scopes: Array<{ key: AggregateScope; label: string; levels: number }>;
  };
}

export type AggregateScope = "all" | "base" | "trunk" | "branch";

export type BaseSemanticType =
  | "product_code"
  | "product_name"
  | "alias"
  | "category"
  | "org_code"
  | "org_name"
  | "time"
  | "other";

export type FeatureLayer = "base" | "trunk" | "branch" | "none";
export type QueryUse = "filter" | "group_by" | "aggregate" | "sort" | "join" | "compare";

export interface BaseFeature {
  source: string;
  name?: string;
  label?: string;
  semantic_type: BaseSemanticType;
}

/** 探测单列画像 + 建议角色 */
export interface ProbeColumn {
  index: number;
  source: string;
  cn: string;
  sample: string | null;
  non_null: number;
  numeric_ratio: number;
  time_ratio: number;
  guess_role: "time" | "metric" | "dimension" | "ignore";
  guess_name: string;
  guess_label: string;
  guess_time_format?: string;
  unique_ratio?: number;
  suggestion_source?: "knowledge" | "ai" | "rule_fallback";
  recommendation_source?: "knowledge" | "ai" | "rule_fallback";
  suggestion_reason?: string;
  feature_role?: "time" | "metric" | "dimension" | "ignore";
  base_candidate?: boolean;
  semantic_type?: BaseSemanticType;
  knowledge_matches?: Array<Record<string, unknown>>;
  knowledge_confidence?: number | null;
  recommendation_sources?: string[];
  ai_conflict?: boolean;
  needs_manual_review?: boolean;
  analysis_version?: string;
  feature_layer?: FeatureLayer;
  feature_layers?: FeatureLayer[];
  query_uses?: QueryUse[];
  evidence?: {
    non_null_rate?: number | null;
    numeric_ratio?: number;
    time_ratio?: number;
    unique_ratio?: number;
    sample_values?: string[];
    [key: string]: unknown;
  };
  /** 引擎保留列名（动态表主键等）：不能作为通用数据集的层级/指标 */
  reserved?: boolean;
}

export interface ProbeResult {
  file_name: string;
  file_token: string;
  sheet: string;
  sheets: string[];
  header_mode: "single" | "double";
  header_rows: number;
  row_count: number;
  row_count_estimated: boolean;
  row_count_capped: boolean;
  columns: ProbeColumn[];
  preview: Record<string, string | null>[];
  time_column: string | null;
  time_format: string;
  default_derive: string[];
  source?: { kind: string; datasource_id?: string; table?: string };
  analysis_source?: "knowledge" | "ai" | "ai+knowledge" | "rule_fallback";
  recommendation_catalog?: {
    matched_fields: number;
    total_fields: number;
  };
}

/** 向导草案中勾选后的列状态（前端状态 → 提交体） */
export interface WizardColumn extends ProbeColumn {
  role: "ignore" | "time" | "level" | "metric" | "fact";
  name: string;   // 物理列名（层级/指标）
  label: string;  // 中文名
  levelKey: string; // 层级标识（role=level）
  timeFormat: string;
  baseFeature: boolean;
  semanticType: BaseSemanticType;
  featureLayer: FeatureLayer;
  featureLayers: Exclude<FeatureLayer, "none">[];
  queryUses: QueryUse[];
  recommendationAccepted: boolean;
  included: boolean;
}

export interface DatasetRowColumn {
  name: string;
  label: string;
}

export interface DatasetRowsResponse {
  dataset: string;
  kind: string;
  columns: DatasetRowColumn[];
  total: number;
  page: number;
  page_size: number;
  items: Record<string, string | null>[];
}

export interface ReinitializeDataset {
  dataset: string;
  label: string;
  kind: string;
  source_file: string | null;
  source_exists: boolean;
  current_rows: number;
  capabilities?: Record<string, unknown>;
  profile?: Record<string, unknown>;
  engine_profile?: Record<string, unknown>;
  source_kind?: string;
}

export interface ReinitializeReadiness {
  ready: boolean;
  blockers: string[];
  catalog: {
    ok: boolean;
    path?: string;
    version?: number;
    entries?: number;
    active_entries?: number;
    retired_entries?: number;
    error?: string;
  };
  model: {
    active?: string;
    provider?: string;
    api_key_configured?: boolean;
    agent_reachable?: boolean;
    agent_error?: string | null;
    ready?: boolean;
  };
  datasets: Array<{
    dataset: string;
    kind: string;
    source?: string | Record<string, unknown> | null;
    ok: boolean;
    detail: string;
  }>;
  running_import_tasks: string[];
  stale_import_tasks?: string[];
  reinitialize_state?: Record<string, unknown>;
  dataset_count: number;
  mode: string;
  manual_clear_available?: boolean;
}

export interface ReinitializePreview {
  model: Record<string, unknown>;
  datasets: ReinitializeDataset[];
  warnings: string[];
  proposals?: Record<string, unknown>[];
  analysis_mode?: string;
  field_proposals_created?: Record<string, unknown>[];
  reinitialize_state?: {
    status?: string;
    started_at?: string;
    cleared_at?: string;
    backup_path?: string;
    next_step?: string;
  };
}

export interface ReinitializeTask {
  dataset: string;
  task_uuid: string;
  file_name: string;
}

export interface ReinitializeResult {
  message: string;
  backup_path: string;
  tasks: ReinitializeTask[];
  expected_datasets?: string[];
  state?: Record<string, unknown>;
}

/** 层级值检索响应（matched: exact 精确命中 / contains 包含回退 / all 无条件全量） */
export interface LevelValuesResponse {
  dataset: string;
  level: string;
  level_label: string;
  query: string | null;
  matched: "exact" | "contains" | "all";
  items: { value: string; record_count: number }[];
}

export interface DatasourceTable {
  name: string;
  schema?: string;
  [key: string]: unknown;
}

export interface DatasourceTablePreview {
  source_id: string;
  table: string;
  columns: { name: string; type?: string; nullable?: string; label?: string }[];
  rows: Record<string, unknown>[];
  sampled: number;
}

export interface ModelProvider {
  id: string;
  name?: string;
  base_url?: string;
  model?: string;
  api_key_configured?: boolean;
  supports_images?: boolean;
  [key: string]: unknown;
}

export interface FoundationField {
  source: string;
  name: string;
  label: string;
  semantic_type: string;
  layer: "base" | string;
  enabled: boolean;
  recommendation_source: "experience_yaml" | "ai" | "rule_fallback" | "manual" | string;
  reason?: string;
}

export interface FoundationSuggestion {
  id: string;
  dataset: string;
  fields: FoundationField[];
  rationale?: string;
  source?: string;
  status: "pending_review" | "approved" | "rejected" | string;
}

export interface RuleSuggestion {
  id: string;
  name: string;
  source: string;
  precision?: string;
  mapping?: Record<string, string>;
  rules?: Array<{ condition: string; value: string }>;
  rationale?: string;
  source_type?: string;
  status: "pending_review" | "approved" | "rejected" | string;
}

export interface FoundationConfig {
  version: number;
  catalog: string;
  updated_at: string;
  global_semantics: Record<string, unknown>;
  experience_recommendations: Record<string, unknown>[];
  dataset_bindings: Record<string, FoundationField[]>;
  temporal_bindings?: Record<string, FoundationField[]>;
  pending_suggestions: FoundationSuggestion[];
  read_only?: boolean;
  source?: string;
  asset_id?: string;
  profile_id?: string;
  profile_version?: number;
  storage_type?: string;
  catalog_id?: string;
}

export interface ModelConfig {
  version: number;
  active: string;
  providers: ModelProvider[];
  source?: string;
  engine_llm_boundary?: string;
  fallback?: Record<string, unknown>;
}

// ---------------- 系统参数 ----------------

export interface SystemParamItem {
  key: string;
  label: string;
  description: string;
  type: string;
  min: number;
  max: number;
  value: number;
  default: number;
  source: "interface" | "env" | "default";
}

export interface SystemParamsResponse {
  items: SystemParamItem[];
  read_only?: boolean;
  profile_id?: string;
  profile_version?: number;
  storage_type?: string;
  catalog_id?: string;
}

export interface SystemParamsUpdateResponse extends SystemParamsResponse {
  params: Record<string, number>;
  message: string;
}

// ---------------- AI 数据引擎与数据集运行配置档 ----------------

export interface RuntimeStorageSpec {
  type: "legacy_sqlite" | "cnts" | string;
  adapter_version: string;
  root: string;
  contract_version: number;
}

export interface RuntimeCatalogSpec {
  catalog_id: string;
  catalog_hash: string;
  datasets: Record<string, string>;
}

export interface RuntimeAcceptanceSpec {
  status: "not_run" | "running" | "passed" | "failed" | string;
  report_id?: string | null;
  passed_at?: string | null;
  checks: Record<string, unknown>;
}

export interface RuntimeProfile {
  profile_id: string;
  display_name: string;
  environment: string;
  state: string;
  effective_state: string;
  profile_version: number;
  storage: RuntimeStorageSpec;
  catalog: RuntimeCatalogSpec;
  semantics: Record<string, unknown>;
  topology: Record<string, unknown>;
  acceptance: RuntimeAcceptanceSpec;
  created_at: string;
  updated_at: string;
  is_active: boolean;
}

export interface RuntimeProfilePointer {
  profile_id: string;
  profile_version: number;
  revision: number;
  activated_at: string;
  reason?: string;
}

export interface RuntimeProfilesResponse {
  active: RuntimeProfile;
  pointer: RuntimeProfilePointer;
  items: RuntimeProfile[];
}

export interface RuntimeAuditEvent {
  event_id: string;
  timestamp: string;
  action: string;
  actor: string;
  reason: string;
  pointer_revision?: number | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
}
