import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Descriptions,
  Divider,
  Form,
  Input,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import {
  listDatasources,
  listDatasourceTables,
  getDataset,
  importDataset,
  probeDatabaseDataset,
  probeDataset,
  upsertDataset,
} from "../api/endpoints";
import type {
  BaseSemanticType,
  DatasetSummary,
  FeatureLayer,
  ProbeResult,
  QueryUse,
  WizardColumn,
} from "../api/types";
import ImportProgress from "../components/ImportProgress";
import { useImportTask } from "../hooks/useImportTask";

/** 可自动派生的时间层级（与后端 DERIVE_ALLOWED 对应） */
const DERIVE_META: Record<string, { label: string; keyOf: string }> = {
  year: { label: "年", keyOf: "year" },
  season: { label: "季节", keyOf: "season" },
  month: { label: "月", keyOf: "yyyymm" },
  week: { label: "周", keyOf: "week" },
  day: { label: "日", keyOf: "day" },
  quarter: { label: "季度", keyOf: "quarter" },
  half_year: { label: "上半年/下半年", keyOf: "half_year" },
  month_parity: { label: "单月/双月", keyOf: "month_parity" },
  solar_term: { label: "二十四节气", keyOf: "solar_term" },
};

const DERIVE_KEYS = Object.keys(DERIVE_META);

const MONTH_GRANULARITY_FORMATS = ["yyyymm", "yyyy-mm"];

const ROLE_OPTIONS_GENERIC = [
  { value: "ignore", label: "忽略" },
  { value: "time", label: "时间列" },
  { value: "level", label: "维度层级" },
  { value: "metric", label: "指标" },
  { value: "fact", label: "事实列(不建层)" },
];

const ROLE_OPTIONS_DIM = [
  { value: "ignore", label: "忽略" },
  { value: "level", label: "维度列" },
];

const BASE_SEMANTIC_OPTIONS: { value: BaseSemanticType; label: string }[] = [
  { value: "product_code", label: "产品编码" },
  { value: "product_name", label: "产品名称" },
  { value: "alias", label: "别名" },
  { value: "category", label: "类别" },
  { value: "org_code", label: "组织编码" },
  { value: "org_name", label: "组织名称" },
  { value: "time", label: "时间" },
  { value: "other", label: "其他" },
];

const FEATURE_LAYER_OPTIONS: { value: FeatureLayer; label: string }[] = [
  { value: "base", label: "基座" },
  { value: "trunk", label: "中通主干" },
  { value: "branch", label: "中通分枝" },
  { value: "none", label: "忽略" },
];

const QUERY_USE_LABELS: Record<QueryUse, string> = {
  filter: "过滤",
  group_by: "分组",
  aggregate: "聚合",
  sort: "排序",
  join: "关联",
  compare: "对比",
};

const QUERY_USE_OPTIONS = (Object.entries(QUERY_USE_LABELS) as [QueryUse, string][]).map(
  ([value, label]) => ({ value, label })
);

const FEATURE_LAYER_LABELS: Record<FeatureLayer, string> = {
  base: "基座",
  trunk: "中通主干",
  branch: "中通分枝",
  none: "忽略",
};

type ActiveFeatureLayer = Exclude<FeatureLayer, "none">;

const ACTIVE_FEATURE_LAYERS: ActiveFeatureLayer[] = ["base", "trunk", "branch"];

function normalizeFeatureLayers(
  layers: FeatureLayer[] | undefined,
  fallback: FeatureLayer,
): ActiveFeatureLayer[] {
  const selected = (layers || []).filter(
    (layer): layer is ActiveFeatureLayer => ACTIVE_FEATURE_LAYERS.includes(layer as ActiveFeatureLayer),
  );
  if (selected.length) return Array.from(new Set(selected));
  return fallback === "none" ? [] : [fallback];
}

function toWizardColumns(probe: ProbeResult): WizardColumn[] {
  return probe.columns.map((c) => ({
    ...c,
    role:
      c.guess_role === "time" || c.source === probe.time_column
        ? "time"
        : c.guess_role === "metric"
          ? "metric"
          : c.guess_role === "dimension"
            ? "level"
            : "ignore",
    name: c.guess_name,
    label: c.guess_label,
    levelKey: c.guess_name,
    timeFormat: c.guess_time_format || probe.time_format || "yyyymm",
    baseFeature: Boolean(c.base_candidate),
    semanticType: c.semantic_type || "other",
    featureLayer: normalizeFeatureLayers(
      c.feature_layers,
      c.feature_layer || (c.base_candidate ? "base" : c.guess_role === "metric" ? "branch" : c.guess_role === "ignore" ? "none" : "trunk"),
    )[0] || "none",
    featureLayers: normalizeFeatureLayers(
      c.feature_layers,
      c.feature_layer || (c.base_candidate ? "base" : c.guess_role === "metric" ? "branch" : c.guess_role === "ignore" ? "none" : "trunk"),
    ),
    queryUses: c.query_uses || [],
    recommendationAccepted: c.guess_role !== "ignore",
    included: c.guess_role !== "ignore",
  }));
}

/** 已保存声明里的字段空间归属（base/trunk/branch 条目按 source 汇总） */
interface SavedFeatureInfo {
  source: string;
  name?: string;
  label?: string;
  semantic_type?: BaseSemanticType;
  query_uses?: QueryUse[];
  layers?: ActiveFeatureLayer[];
}

function savedFeatureMap(decl: DatasetSummary): Map<string, SavedFeatureInfo> {
  const capabilities = (decl.capabilities || {}) as Record<string, unknown>;
  const featureLayers = (capabilities.feature_layers || {}) as Record<string, unknown>;
  const map = new Map<string, SavedFeatureInfo>();
  for (const layer of ACTIVE_FEATURE_LAYERS) {
    const items = (featureLayers[layer] || []) as SavedFeatureInfo[];
    for (const item of items) {
      if (!item?.source) continue;
      const prev: SavedFeatureInfo = map.get(item.source) || { source: item.source };
      map.set(item.source, {
        ...prev,
        source: item.source,
        name: item.name || prev.name,
        label: item.label || prev.label,
        semantic_type: item.semantic_type || prev.semantic_type,
        query_uses: item.query_uses?.length ? item.query_uses : prev.query_uses,
        layers: [...(prev.layers || []), layer],
      });
    }
  }
  return map;
}

/**
 * 编辑声明模式：探测新文件后，用已保存的字段角色覆盖画像建议。
 * 匹配依据是 Excel 表头（source），表头变了就退回建议角色，由人工重新勾选。
 */
function applySavedDeclaration(
  wc: WizardColumn[],
  decl: DatasetSummary,
): { cols: WizardColumn[]; derive: string[]; levelOrder: string[] } {
  const columnSources = (decl.column_sources || {}) as Record<string, string>;
  const levelValueKey = (decl.level_value_key || {}) as Record<string, string>;
  const levelLabels = (decl.level_labels || {}) as Record<string, string>;
  const features = savedFeatureMap(decl);

  const declaredLevels = (decl.levels || [])
    .map((k) => String(k))
    .filter((k) => !DERIVE_KEYS.includes(k));
  const derive = ((decl.derive || []) as unknown[])
    .map(String)
    .filter((k) => DERIVE_KEYS.includes(k));

  const levelKeyBySource = new Map<string, string>();
  const levelColumns = new Set<string>();
  for (const key of declaredLevels) {
    const column = levelValueKey[key] || key;
    levelColumns.add(column);
    const source = columnSources[column];
    if (source) levelKeyBySource.set(source, key);
  }

  const metricBySource = new Map<string, { name: string; label: string }>();
  const metricColumns = new Set<string>();
  for (const m of decl.metrics || []) {
    metricColumns.add(m.name);
    if (!m.source) continue;
    metricBySource.set(String(m.source), { name: m.name, label: m.label || m.name });
  }

  const factBySource = new Map<string, { name: string; label: string }>();
  for (const column of ((decl.fact_columns || []) as unknown[]).map(String)) {
    if (levelColumns.has(column) || metricColumns.has(column)) continue;
    const source = columnSources[column];
    if (!source) continue;
    factBySource.set(source, { name: column, label: features.get(source)?.label || column });
  }

  const dimBySource = new Map<string, { name: string; label: string }>();
  for (const c of decl.columns || []) {
    dimBySource.set(String(c.source), { name: c.name, label: c.label });
  }

  const timeSource = String((decl.time || {}).source || "");
  const timeFormat = String((decl.time || {}).format || "yyyymm");

  const cols = wc.map((c) => {
    const feat = features.get(c.source);
    const layers = feat?.layers || [];
    const overlay: Partial<WizardColumn> = {
      name: feat?.name || c.name,
      label: feat?.label || c.label,
      semanticType: feat?.semantic_type || c.semanticType,
      queryUses: feat?.query_uses || c.queryUses,
      featureLayer: layers[0] || c.featureLayer,
      featureLayers: layers.length ? layers : c.featureLayers,
      baseFeature: layers.includes("base"),
    };
    if (c.source === timeSource) {
      return { ...c, ...overlay, role: "time", included: true, timeFormat } as WizardColumn;
    }
    const metric = metricBySource.get(c.source);
    if (metric) {
      return { ...c, ...overlay, ...metric, role: "metric", included: true } as WizardColumn;
    }
    const levelKey = levelKeyBySource.get(c.source);
    if (levelKey) {
      const column = levelValueKey[levelKey] || levelKey;
      return {
        ...c,
        ...overlay,
        role: "level",
        included: true,
        levelKey,
        name: column,
        label: levelLabels[levelKey] || overlay.label || c.label,
      } as WizardColumn;
    }
    const dim = dimBySource.get(c.source);
    if (dim) {
      return { ...c, ...overlay, ...dim, role: "level", included: true, levelKey: dim.name } as WizardColumn;
    }
    const fact = factBySource.get(c.source);
    if (fact) {
      return { ...c, ...overlay, ...fact, role: "fact", included: true } as WizardColumn;
    }
    return {
      ...c,
      ...overlay,
      role: "ignore",
      included: false,
      featureLayer: "none" as FeatureLayer,
      featureLayers: [],
      baseFeature: false,
    } as WizardColumn;
  });

  return { cols, derive, levelOrder: declaredLevels };
}

/** 向导状态 → 提交给后端的草案（后端定稿物理表名/声明校验） */
function buildDraft(
  id: string,
  label: string,
  kind: string,
  cols: WizardColumn[],
  derive: string[],
  primaryKey: string,
  baseEnabled: boolean,
  source?: Record<string, unknown>,
  levelOrder: string[] = [],
): Record<string, unknown> {
  const activeCols = cols.filter((c) => c.included && c.role !== "ignore");
  const featureItem = (c: WizardColumn) => ({
    source: c.source,
    name: c.name,
    label: c.label,
    role: c.role === "level" || c.role === "fact" ? "dimension" : c.role,
    semantic_type: c.semanticType,
    query_uses: c.queryUses,
    recommended: c.recommendationAccepted,
  });
  // 一个字段可以同时落入多个结构层；未启用基座时，基座候选降级到通用层。
  const resolvedLayers = (c: WizardColumn): ActiveFeatureLayer[] => {
    const layers = normalizeFeatureLayers(c.featureLayers, c.featureLayer);
    const filtered = layers.filter((layer) => baseEnabled || layer !== "base");
    if (filtered.length) return filtered;
    return [c.role === "metric" ? "branch" : "trunk"];
  };
  const layerOf = (layer: Exclude<FeatureLayer, "none">) =>
    activeCols
      .filter((c) => resolvedLayers(c).includes(layer))
      // 时间字段是通用事实的主干入口，不得被声明为基座字段。
      .filter((c) => layer !== "base" || (c.role !== "time" && c.semanticType !== "time"))
      .map(featureItem);
  const featureLayers = {
    base: layerOf("base"),
    trunk: layerOf("trunk"),
    branch: layerOf("branch"),
    dna_keys: Array.from(new Set(activeCols.flatMap((c) => [c.source]))),
    analysis_version: "universal-field-review-v1",
    reviewed: false,
  };
  if (kind === "dimension") {
    return {
      kind: "dimension",
      label,
      source,
      primary_key: primaryKey,
      ...(baseEnabled
        ? {
            base: {
              enabled: true,
              features: cols
                .filter((c) => c.included && baseEnabled && resolvedLayers(c).includes("base"))
                .map((c) => ({
                  source: c.source,
                  name: c.name,
                  label: c.label,
                  semantic_type: c.semanticType,
                })),
            },
          }
        : {}),
      columns: cols
        .filter((c) => c.included && (c.role === "level" || (baseEnabled && resolvedLayers(c).includes("base"))))
        .map((c) => ({ source: c.source, name: c.name, label: c.label })),
      capabilities: { feature_layers: featureLayers },
    };
  }
  const timeCol = cols.find((c) => c.role === "time");
  const orderIndex = (key: string) => {
    const i = levelOrder.indexOf(key);
    return i < 0 ? levelOrder.length : i;
  };
  const levels: Record<string, unknown>[] = [
    ...derive
      .filter((d) => DERIVE_META[d])
      .map((d) => ({
        key: d,
        label: DERIVE_META[d].label,
        derive: true,
        key_of: DERIVE_META[d].keyOf,
      })),
    ...cols
      .filter((c) => c.included && c.role === "level")
      .map((c) => ({
        key: c.levelKey,
        label: c.label,
        source: c.source,
        name: c.name,
      }))
      .sort((a, b) => orderIndex(String(a.key)) - orderIndex(String(b.key))),
  ];
  return {
    kind: "generic",
    label,
    source,
    time: { source: timeCol?.source, format: timeCol?.timeFormat || "yyyymm" },
    levels,
    metrics: cols
      .filter((c) => c.included && c.role === "metric")
      .map((c) => ({ name: c.name, label: c.label, source: c.source })),
    // 事实列：随事实行保存但不进入层级链（名称、备注等，避免污染下钻路径）
    columns: cols
      .filter((c) => c.included && c.role === "fact")
      .map((c) => ({ source: c.source, name: c.name, label: c.label })),
    ...(baseEnabled
      ? {
          base: {
            enabled: true,
            features: cols
              .filter((c) => c.included && resolvedLayers(c).includes("base"))
              .map((c) => ({
                source: c.source,
                name: c.name,
                label: c.label,
                semantic_type: c.semanticType,
              })),
          },
        }
      : {}),
    capabilities: { feature_layers: featureLayers },
  };
}

export default function DatasetWizard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const editId = searchParams.get("edit") || "";
  const [step, setStep] = useState(0);
  const [meta, setMeta] = useState({ id: "", label: "", kind: "generic" });
  const [declaration, setDeclaration] = useState<DatasetSummary | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sourceKind, setSourceKind] = useState<"excel" | "database">("excel");
  const [datasources, setDatasources] = useState<Record<string, unknown>[]>([]);
  const [databaseSourceId, setDatabaseSourceId] = useState("");
  const [databaseTables, setDatabaseTables] = useState<{ name: string; schema?: string }[]>([]);
  const [databaseTable, setDatabaseTable] = useState("");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [cols, setCols] = useState<WizardColumn[]>([]);
  const [derive, setDerive] = useState<string[]>([]);
  const [levelOrder, setLevelOrder] = useState<string[]>([]);
  const [primaryKey, setPrimaryKey] = useState("");
  const [baseEnabled, setBaseEnabled] = useState(false);
  const [mode, setMode] = useState("replace");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const [onlyActive, setOnlyActive] = useState(false);
  const [analysisConfirmed, setAnalysisConfirmed] = useState(false);
  const [registered, setRegistered] = useState(false);
  const { task, percent, running, watch } = useImportTask();

  useEffect(() => {
    if (searchParams.get("source") !== "database") return;
    setSourceKind("database");
    setDatabaseSourceId(searchParams.get("datasource") || "");
    setDatabaseTable(searchParams.get("table") || "");
  }, [searchParams]);

  // 编辑声明模式：载入现有数据集声明，探测到新文件后按表头覆盖字段角色
  useEffect(() => {
    if (!editId) return;
    void getDataset(editId)
      .then((d) => {
        setDeclaration(d);
        setMeta({
          id: String(d.id || editId),
          label: String(d.label || ""),
          kind: String(d.kind || "generic"),
        });
        const base = (d.base || {}) as { enabled?: boolean };
        setBaseEnabled(Boolean(base.enabled));
        const source = (d.source || {}) as { kind?: string; datasource_id?: string; table?: string };
        if (source.kind === "database") {
          setSourceKind("database");
          setDatabaseSourceId(source.datasource_id || "");
          setDatabaseTable(source.table || "");
        }
      })
      .catch((e) => message.error(errorMessage(e)));
  }, [editId]);

  // 层级链顺序跟随勾选：保留既有顺序，新增层级追加到末尾，取消勾选的移除
  useEffect(() => {
    const keys = cols
      .filter((c) => c.included && c.role === "level" && c.levelKey)
      .map((c) => c.levelKey);
    setLevelOrder((prev) => {
      const kept = prev.filter((k) => keys.includes(k));
      const added = keys.filter((k) => !kept.includes(k));
      const next = [...kept, ...added];
      if (next.length === prev.length && next.every((k, i) => k === prev[i])) return prev;
      return next;
    });
  }, [cols]);

  const draft = useMemo(
    () => buildDraft(
      meta.id,
      meta.label,
      meta.kind,
      cols,
      derive,
      primaryKey,
      baseEnabled,
      sourceKind === "database"
        ? { kind: "database", datasource_id: databaseSourceId, table: databaseTable }
        : { kind: "excel", file_name: file?.name || "" },
      levelOrder,
    ),
    [meta, cols, derive, primaryKey, baseEnabled, sourceKind, databaseSourceId, databaseTable, levelOrder]
  );

  const patch = (index: number, changes: Partial<WizardColumn>) => {
    setCols((prev) => prev.map((c, i) => (i === index ? { ...c, ...changes } : c)));
  };

  /** 单列改角色：带保留列守卫（物理名/层级键/中文名同步刷新建议值） */
  const roleChanges = (
    col: WizardColumn,
    role: WizardColumn["role"]
  ): Partial<WizardColumn> => {
    const changes: Partial<WizardColumn> = {
      role,
      included: role !== "ignore",
      recommendationAccepted: false,
      ...(role === "ignore"
        ? { baseFeature: false, featureLayer: "none" as FeatureLayer, featureLayers: [] }
        : {
            featureLayer: role === "metric" ? "branch" : col.featureLayer === "none" ? "trunk" : col.featureLayer,
            featureLayers: role === "metric"
              ? ["branch"]
              : normalizeFeatureLayers(col.featureLayers, col.featureLayer).length
                ? normalizeFeatureLayers(col.featureLayers, col.featureLayer)
                : ["trunk"],
          }),
    };
    if (role === "level") {
      changes.name = col.guess_name;
      changes.levelKey = col.guess_name;
      changes.label = col.guess_label;
    } else if (role === "metric" || role === "fact") {
      changes.name = col.guess_name;
      changes.label = col.guess_label;
    } else if (role === "time") {
      changes.timeFormat = col.guess_time_format || probe?.time_format || "yyyymm";
    }
    return changes;
  };

  const canTakeRole = (col: WizardColumn, role: WizardColumn["role"]) =>
    !(meta.kind === "generic" && col.reserved && (role === "level" || role === "metric" || role === "fact"));

  const onRoleChange = (index: number, role: WizardColumn["role"]) => {
    const col = cols[index];
    if (!canTakeRole(col, role)) {
      message.warning(`列 ${col.source} 是引擎保留列名，不能作为层级/指标/事实列，请改为忽略或改物理列名`);
      return;
    }
    setCols((prev) =>
      prev.map((c, i) => {
        if (i === index) return { ...c, ...roleChanges(c, role) };
        // 时间列唯一：其余时间列退回忽略
        if (role === "time" && c.role === "time") return { ...c, role: "ignore" };
        return c;
      })
    );
  };

  const onLayerChange = (index: number, featureLayers: FeatureLayer[]) => {
    const current = cols[index];
    if (current?.role === "time" && featureLayers.includes("base")) {
      message.warning("时间字段属于通用事实的中通主干，不能作为基座特征；基座只负责时间语义翻译");
      featureLayers = featureLayers.filter((layer) => layer !== "base");
    }
    setCols((prev) => prev.map((c, i) => {
      if (i !== index) return c;
      const activeLayers = normalizeFeatureLayers(featureLayers, "none");
      if (!activeLayers.length) {
        return { ...c, featureLayer: "none", featureLayers: [], included: false, role: "ignore", baseFeature: false };
      }
      const inferredRole = c.role === "ignore"
        ? c.guess_role === "time" ? "time" : c.guess_role === "metric" ? "metric" : "level"
        : c.role;
      return {
        ...c,
        featureLayer: activeLayers[0],
        featureLayers: activeLayers,
        included: true,
        role: inferredRole,
        recommendationAccepted: false,
        baseFeature: activeLayers.includes("base"),
      };
    }));
  };

  const onBaseFeatureChange = (index: number, checked: boolean) => {
    setCols((prev) => prev.map((c, i) => {
      if (i !== index) return c;
      if (checked && (c.role === "time" || c.semanticType === "time")) {
        message.warning("时间字段不能作为基座特征，请保留在中通主干");
        return c;
      }
      if (checked && c.role === "ignore") {
        return { ...c, baseFeature: true, featureLayer: "base", featureLayers: ["base"], included: true, recommendationAccepted: false, role: "level", name: c.guess_name, levelKey: c.guess_name, label: c.guess_label };
      }
      const current = normalizeFeatureLayers(c.featureLayers, c.featureLayer);
      const next = checked
        ? Array.from(new Set([...current, "base" as ActiveFeatureLayer]))
        : current.filter((layer) => layer !== "base");
      return {
        ...c,
        baseFeature: checked,
        featureLayer: next[0] || (c.role === "metric" ? "branch" : "trunk"),
        featureLayers: next,
        included: checked || c.included,
        recommendationAccepted: false,
      };
    }));
  };

  const applyIncludedChange = (index: number, checked: boolean) => {
    const target = cols[index];
    setCols((prev) => prev.map((c, i) => {
      if (i !== index) return c;
      if (!checked) return { ...c, included: false, role: "ignore", featureLayer: "none", featureLayers: [], baseFeature: false, recommendationAccepted: false };
      const role = c.guess_role === "time" ? "time" : c.guess_role === "metric" ? "metric" : "level";
      const featureLayer = c.feature_layer || (c.base_candidate ? "base" : role === "metric" ? "branch" : "trunk");
      const featureLayers = normalizeFeatureLayers(c.feature_layers, featureLayer);
      return { ...c, included: true, role, featureLayer: featureLayers[0] || "trunk", featureLayers, recommendationAccepted: false };
    }));
    if (!checked && target?.source === primaryKey) setPrimaryKey("");
  };

  const onIncludedChange = (index: number, checked: boolean) => {
    if (checked) {
      applyIncludedChange(index, true);
      return;
    }
    const col = cols[index];
    const reasons = col ? requiredReasons(col) : [];
    if (!reasons.length) {
      applyIncludedChange(index, false);
      return;
    }
    Modal.confirm({
      title: "确认取消必需字段？",
      content: (
        <div>
          <div style={{ marginBottom: 6 }}>你正在取消“{col.source}”的纳入声明：</div>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </div>
      ),
      okText: "继续取消",
      cancelText: "保留勾选",
      okButtonProps: { danger: true },
      onOk: () => applyIncludedChange(index, false),
    });
  };

  /** 批量设置角色（勾选后一键应用）：跳过保留列；time 只取第一个 */
  const setRolesBulk = (targets: number[], role: WizardColumn["role"]) => {
    const skipped = targets.filter((i) => cols[i] && !canTakeRole(cols[i], role));
    let valid = targets.filter((i) => cols[i] && canTakeRole(cols[i], role));
    if (skipped.length) {
      message.warning(
        `${skipped.length} 个引擎保留列（如 id）不能作为层级/指标，已跳过`
      );
    }
    if (role === "time" && valid.length > 1) {
      message.info("时间列只能有一个，已设置到第一个勾选列");
      valid = valid.slice(0, 1);
    }
    if (!valid.length) return;
    setCols((prev) =>
      prev.map((c, i) => {
        if (valid.includes(i)) return { ...c, ...roleChanges(c, role) };
        if (role === "time" && c.role === "time") return { ...c, role: "ignore" };
        return c;
      })
    );
    setSelected((prev) => prev.filter((k) => !valid.includes(k)));
  };

  const filteredCols = useMemo(
    () =>
      cols.filter((c) => {
        if (onlyActive && !c.included) return false;
        if (!query) return true;
        const q = query.toLowerCase();
        return (
          c.source.toLowerCase().includes(q) ||
          (c.cn || "").includes(query) ||
          (c.label || "").toLowerCase().includes(q)
        );
      }),
    [cols, query, onlyActive]
  );

  const roleStat = useMemo(() => {
    const stat = { time: 0, level: 0, metric: 0, fact: 0 };
    for (const c of cols) {
      if (c.role === "time") stat.time++;
      else if (c.role === "level") stat.level++;
      else if (c.role === "metric") stat.metric++;
      else if (c.role === "fact") stat.fact++;
    }
    return stat;
  }, [cols]);

  const layerStat = useMemo(() => {
    const stat: Record<FeatureLayer, number> = { base: 0, trunk: 0, branch: 0, none: 0 };
    for (const c of cols) {
      if (!c.included) continue;
      const layers = normalizeFeatureLayers(c.featureLayers, c.featureLayer);
      if (!layers.length) stat.none++;
      for (const layer of layers) stat[layer] += 1;
    }
    return stat;
  }, [cols]);

  /** 当前字段取消“纳入声明”时，给出会受影响的约束说明。 */
  const requiredReasons = (col: WizardColumn): string[] => {
    if (!col.included) return [];
    const reasons: string[] = [];
    const includedMetrics = cols.filter((item) => item.included && item.role === "metric");
    const includedLevels = cols.filter((item) => item.included && item.role === "level");
    const includedBase = cols.filter((item) => item.included && item.baseFeature);

    if (meta.kind === "generic") {
      if (col.role === "time") {
        reasons.push("通用事实数据集必须有一个时间列；取消后将无法保存声明。");
      }
      if (col.role === "metric" && includedMetrics.length <= 1) {
        reasons.push("通用事实数据集至少需要一个指标；取消后将无法保存声明。");
      }
      if (col.role === "level" && !derive.length && includedLevels.length <= 1) {
        reasons.push("当前没有自动派生时间层级，至少需要一个维度层级；取消后将无法保存声明。");
      }
    } else if (col.source === primaryKey) {
      reasons.push("这是维表/主数据的主键列；取消后需要重新指定主键才能保存声明。");
    }

    if (baseEnabled && col.baseFeature && includedBase.length <= 1) {
      reasons.push("这是当前唯一的基座特征；取消后将失去基座过滤/翻译能力，且无法保存基座声明。");
    }
    return reasons;
  };

  const requiredLabel = (col: WizardColumn) => (
    requiredReasons(col).length ? <Tag color="red">必需</Tag> : null
  );

  const recommendationStat = useMemo(() => ({
    recommended: cols.filter((c) => c.included).length,
    conflicts: cols.filter((c) => c.ai_conflict).length,
    evidence: cols.filter((c) => c.suggestion_reason || c.evidence).length,
  }), [cols]);

  useEffect(() => {
    if (sourceKind !== "database") return;
    void listDatasources()
      .then((r) => setDatasources(r.items))
      .catch((e) => message.error(errorMessage(e)));
  }, [sourceKind]);

  useEffect(() => {
    if (sourceKind !== "database" || !databaseSourceId) {
      setDatabaseTables([]);
      return;
    }
    void listDatasourceTables(databaseSourceId)
      .then((r) => setDatabaseTables(r.items as { name: string; schema?: string }[]))
      .catch((e) => message.error(errorMessage(e)));
  }, [sourceKind, databaseSourceId]);

  // ---------------- 第一步：上传/选择表 + 探测 ----------------
  const doProbe = useCallback(async () => {
    if (sourceKind === "excel" && !file) {
      message.warning("请先选择 Excel 文件");
      return;
    }
    if (sourceKind === "database" && (!databaseSourceId || !databaseTable)) {
      message.warning("请选择已配置数据源和数据库表");
      return;
    }
    if (!/^[a-z][a-z0-9_]{0,39}$/.test(meta.id)) {
      message.warning("数据集 id 必须为小写字母开头的英文标识（可含数字/下划线）");
      return;
    }
    setBusy(true);
    try {
      const r = sourceKind === "database"
        ? await probeDatabaseDataset(databaseSourceId, databaseTable)
        : await probeDataset(file as File);
      let wc = toWizardColumns(r);
      let nextDerive = r.default_derive || [];
      if (declaration) {
        const applied = applySavedDeclaration(wc, declaration);
        wc = applied.cols;
        if (applied.derive.length) nextDerive = applied.derive;
        setLevelOrder(applied.levelOrder);
      } else {
        setLevelOrder([]);
      }
      setProbe(r);
      setCols(wc);
      setDerive(nextDerive);
      setPrimaryKey(
        (declaration?.primary_key as string | undefined) || wc[0]?.source || ""
      );
      setAnalysisConfirmed(false);
      setRegistered(false);
      setSelected([]);
      setStep(1);
      message.success(
        declaration
          ? `探测完成：${r.row_count} 行 / ${r.columns.length} 列，已按原声明回填字段角色`
          : `探测完成：${r.row_count} 行 / ${r.columns.length} 列`
      );
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [file, meta.id, sourceKind, databaseSourceId, databaseTable, declaration]);

  // ---------------- 第三步：只保存声明（不导入） ----------------
  const saveDeclaration = async () => {
    const levels = (draft.levels as unknown[]) || [];
    const metrics = (draft.metrics as unknown[]) || [];
    if (!analysisConfirmed) {
      message.warning("请先完成字段分析人工确认");
      return;
    }
    if (baseEnabled && !cols.some((c) => c.included && c.baseFeature)) {
      message.warning("已启用基座数据源，请至少选择一个基座特征字段");
      return;
    }
    if (meta.kind === "generic") {
      if (!(draft.time as { source?: string })?.source) {
        message.warning("请在「勾选字段」中指定一个时间列");
        return;
      }
      if (levels.length === 0) {
        message.warning("至少需要一个层级（自动派生或维度层级）");
        return;
      }
      if (metrics.length === 0) {
        message.warning("至少需要一个指标");
        return;
      }
    } else {
      if (!primaryKey) {
        message.warning("请指定主键列");
        return;
      }
      if ((draft.columns as unknown[]).length === 0) {
        message.warning("至少需要一列维度列");
        return;
      }
    }

    setBusy(true);
    try {
      const reviewedDraft = {
        ...draft,
        capabilities: {
          ...((draft.capabilities as Record<string, unknown>) || {}),
          feature_layers: {
            ...(((draft.capabilities as Record<string, unknown>)?.feature_layers as Record<string, unknown>) || {}),
            reviewed: true,
          },
        },
      };
      await upsertDataset(meta.id, reviewedDraft);
      setRegistered(true);
      setStep(3);
      message.success("数据集声明已保存；尚未导入数据，请在下一步明确点击开始导入");
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  // ---------------- 第四步：用户明确点击后才导入 ----------------
  const startImport = async () => {
    if (!registered) {
      message.warning("请先保存数据集声明");
      return;
    }
    setBusy(true);
    try {
      const { task_uuid } = await importDataset(
        meta.id,
        mode,
        sourceKind === "database" ? {} : { fileToken: probe?.file_token },
      );
      await watch(task_uuid);
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const previewColumns = (probe?.columns || []).map((c) => ({
    title: c.cn ? `${c.cn}(${c.source})` : c.source,
    dataIndex: c.source,
    ellipsis: true,
  }));

  const colTableColumns = [
    {
      title: "纳入声明",
      width: 90,
      fixed: "left" as const,
      render: (_: unknown, r: WizardColumn) => (
        <Space size={4}>
          <Checkbox
            checked={r.included}
            onChange={(e) => onIncludedChange(r.index, e.target.checked)}
          >
            采用
          </Checkbox>
          {requiredLabel(r)}
        </Space>
      ),
    },
    {
      title: "列（Excel 表头）",
      dataIndex: "source",
      width: 170,
      render: (_: string, r: WizardColumn) => (
        <span>
          {r.source}
          {r.cn && <Tag style={{ marginLeft: 6 }}>{r.cn}</Tag>}
          {r.reserved && (
            <Tag color="red" style={{ marginLeft: 6 }}>
              引擎保留列
            </Tag>
          )}
        </span>
      ),
    },
    { title: "样例", dataIndex: "sample", width: 120, ellipsis: true },
    {
      title: "画像",
      width: 230,
      render: (_: unknown, r: WizardColumn) => (
        <Space direction="vertical" size={0}>
          <span style={{ fontSize: 12, color: "#888" }}>
            数值 {Math.round(r.numeric_ratio * 100)}% / 时间 {Math.round(r.time_ratio * 100)}%
          </span>
          <Space size={4} wrap>
            {r.suggestion_source === "knowledge" && <Tag color="blue">经验推荐</Tag>}
            {r.suggestion_source === "ai" && <Tag color="geekblue">AI 建议</Tag>}
            {!r.suggestion_source || r.suggestion_source === "rule_fallback" ? <Tag>规则回退</Tag> : null}
            {r.ai_conflict && <Tag color="orange">AI 冲突，需确认</Tag>}
          </Space>
          <span style={{ fontSize: 12, color: r.ai_conflict ? "#d46b08" : r.suggestion_source === "ai" ? "#1677ff" : r.suggestion_source === "knowledge" ? "#1677ff" : "#999" }}>
            {r.suggestion_reason || "字段画像建议"}
          </span>
          {r.evidence && (
            <span style={{ fontSize: 11, color: "#888" }}>
              证据：非空 {r.evidence.non_null_rate == null ? "—" : `${Math.round(Number(r.evidence.non_null_rate) * 100)}%`} / 唯一值 {Math.round(Number(r.evidence.unique_ratio || 0) * 100)}%
            </span>
          )}
        </Space>
      ),
    },
    {
      title: "推荐层级",
      width: 220,
      render: (_: unknown, r: WizardColumn) => (
        <Select
          mode="multiple"
          size="small"
          style={{ width: 210 }}
          value={normalizeFeatureLayers(r.featureLayers, r.featureLayer)}
          disabled={!r.included}
          onChange={(value) => onLayerChange(r.index, value as FeatureLayer[])}
          options={FEATURE_LAYER_OPTIONS.filter((option) => option.value !== "none")}
          maxTagCount={2}
          placeholder="选择结构层"
        />
      ),
    },
    {
      title: "查询用途",
      width: 180,
      render: (_: unknown, r: WizardColumn) => (
        <Select
          mode="multiple"
          size="small"
          style={{ width: 170 }}
          value={r.queryUses}
          disabled={!r.included}
          onChange={(value) => patch(r.index, { queryUses: value as QueryUse[] })}
          options={QUERY_USE_OPTIONS}
          maxTagCount={2}
          placeholder="选择用途"
        />
      ),
    },
    {
      title: "字段角色",
      dataIndex: "role",
      width: 130,
      render: (v: string, r: WizardColumn) => (
        <Select
          size="small"
          style={{ width: 110 }}
          value={v}
          onChange={(val) => onRoleChange(r.index, val as WizardColumn["role"])}
          options={meta.kind === "dimension" ? ROLE_OPTIONS_DIM : ROLE_OPTIONS_GENERIC}
        />
      ),
    },
    {
      title: "基座特征",
      width: 110,
      render: (_: unknown, r: WizardColumn) => (
        <Checkbox
          checked={baseEnabled && r.included && r.baseFeature}
          disabled={!baseEnabled || !r.included}
          onChange={(e) => onBaseFeatureChange(r.index, e.target.checked)}
        >
          参与过滤
        </Checkbox>
      ),
    },
    {
      title: "基座语义",
      width: 130,
      render: (_: unknown, r: WizardColumn) => (
        <Select
          size="small"
          style={{ width: 120 }}
          value={r.semanticType}
          disabled={!baseEnabled || !r.included || !r.baseFeature}
          onChange={(value) => patch(r.index, { semanticType: value as BaseSemanticType })}
          options={BASE_SEMANTIC_OPTIONS}
        />
      ),
    },
    {
      title: "物理列名 / 层级标识",
      width: 200,
      render: (_: unknown, r: WizardColumn) =>
        r.role === "time" ? (
          <Select
            size="small"
            style={{ width: 150 }}
            value={r.timeFormat}
            onChange={(v) => patch(r.index, { timeFormat: v })}
            options={[
              { value: "yyyymm", label: "yyyymm（202401）" },
              { value: "yyyymmdd", label: "yyyymmdd（20240131）" },
              { value: "yyyy-mm-dd", label: "yyyy-mm-dd（2024-01-31）" },
              { value: "yyyy-mm", label: "yyyy-mm（2024-01）" },
              { value: "d/m/yyyy", label: "d/m/yyyy（2/1/2025）" },
            ]}
          />
        ) : r.role === "ignore" ? (
          <span style={{ color: "#ccc" }}>—</span>
        ) : (
          <Space direction="vertical" size={2}>
            <Input
              size="small"
              value={r.name}
              onChange={(e) => patch(r.index, { name: e.target.value })}
              placeholder="英文列名，如 store_name"
            />
            {r.role === "level" && meta.kind === "generic" && (
              <Input
                size="small"
                value={r.levelKey}
                onChange={(e) => patch(r.index, { levelKey: e.target.value })}
                placeholder="层级标识，如 station"
              />
            )}
          </Space>
        ),
    },
    {
      title: "中文名",
      width: 160,
      render: (_: unknown, r: WizardColumn) =>
        r.role === "ignore" ? (
          <span style={{ color: "#ccc" }}>—</span>
        ) : (
          <Input
            size="small"
            value={r.label}
            onChange={(e) => patch(r.index, { label: e.target.value })}
          />
        ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card title={editId ? `编辑数据集声明 · ${editId}` : "新数据源注册向导"}>
        <Steps
          current={step}
          items={[
            { title: "上传与基本信息" },
            { title: "预览并勾选字段角色" },
            { title: "确认声明" },
            { title: "导入与进度" },
          ]}
        />
      </Card>

      {step === 0 && (
        <Card title={editId ? "第一步：编辑数据集声明与来源" : "第一步：数据集基本信息与来源"}>
          {editId && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`正在编辑数据集 ${editId} 的声明`}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  <li>已保存的字段角色会在探测新文件后自动回填（按 Excel 表头匹配，匹配不上的列退回系统建议）；</li>
                  <li>数据集 id 不可修改，保存后原声明会被整体覆盖；</li>
                  <li>想支持“上周/某一天/日均”这类问题，请选择带具体日期的日粒度源文件并把时间格式改为 yyyy-mm-dd、yyyymmdd 或 d/m/yyyy，同时勾选“周”“日”派生层；</li>
                  <li>保存声明不会动已有数据，需要再点“开始导入”才会按新声明重建。</li>
                </ul>
              }
            />
          )}
          <Row gutter={16}>
            <Col span={8}>
              <Form layout="vertical">
                <Form.Item label="数据集 id（英文标识，保存后不可改）" required>
                  <Input
                    value={meta.id}
                    disabled={Boolean(editId)}
                    placeholder="non_oil_sales"
                    onChange={(e) =>
                      setMeta({ ...meta, id: e.target.value.trim().toLowerCase() })
                    }
                  />
                </Form.Item>
                <Form.Item label="显示名称" required>
                  <Input
                    value={meta.label}
                    placeholder="业务数据乙"
                    onChange={(e) => setMeta({ ...meta, label: e.target.value })}
                  />
                </Form.Item>
                <Form.Item label="数据集类型">
                  <Radio.Group
                    value={meta.kind}
                    onChange={(e) => setMeta({ ...meta, kind: e.target.value })}
                  >
                    <Radio.Button value="generic">通用事实数据集</Radio.Button>
                    <Radio.Button value="dimension">维表/主数据</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                <Form.Item label="基座数据源属性">
                  <Checkbox
                    checked={baseEnabled}
                    onChange={(e) => setBaseEnabled(e.target.checked)}
                  >
                    将此数据源登记为基座数据
                  </Checkbox>
                  <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>
                    基座不是固定产品表；可由任意数据源提供产品、组织、类别等过滤和翻译特征。
                  </div>
                </Form.Item>
                <Form.Item label="数据来源">
                  <Radio.Group
                    value={sourceKind}
                    onChange={(e) => {
                      setSourceKind(e.target.value);
                      setProbe(null);
                    }}
                  >
                    <Radio.Button value="excel">本地 Excel</Radio.Button>
                    <Radio.Button value="database">数据库表</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                {sourceKind === "excel" ? (
                  <Form.Item label="Excel 文件（.xlsx）">
                    <input
                      type="file"
                      accept=".xlsx"
                      onChange={(e) => setFile(e.target.files?.[0] || null)}
                    />
                  </Form.Item>
                ) : (
                  <>
                    <Form.Item label="已配置数据源" required>
                      <Select
                        value={databaseSourceId || undefined}
                        placeholder="选择数据源"
                        options={datasources.map((item) => ({
                          value: String(item.id || ""),
                          label: `${String(item.name || item.id)}（${String(item.type || "")}）`,
                        }))}
                        onChange={(value) => {
                          setDatabaseSourceId(value);
                          setDatabaseTable("");
                        }}
                      />
                    </Form.Item>
                    <Form.Item label="数据库表/集合" required>
                      <Select
                        value={databaseTable || undefined}
                        placeholder="先选择数据源，再读取表清单"
                        options={databaseTables.map((item) => ({
                          value: item.schema ? `${item.schema}.${item.name}` : item.name,
                          label: item.schema ? `${item.schema}.${item.name}` : item.name,
                        }))}
                        onChange={setDatabaseTable}
                        showSearch
                      />
                    </Form.Item>
                  </>
                )}
                <Space>
                  <Button type="primary" loading={busy} onClick={doProbe}>
                    上传并探测
                  </Button>
                  <Button onClick={() => navigate("/datasets")}>返回列表</Button>
                </Space>
              </Form>
            </Col>
            <Col span={16}>
              <Alert
                type="info"
                showIcon
                message="系统会先读取 Excel 或数据库表的字段与样本，融合经验库、AI 和规则，给出基座/主干/分枝及时间、维度、指标、忽略建议；确认后才允许注册，导入还需单独点击。"
                description={
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    <li>字段建议会融合项目经验推荐库、当前 AI 模型和规则画像；经验库是追加式项目资产，不会因注销或重初始化删除；</li>
                    <li>系统会预勾选建议字段，并在字段表下方展示每个勾选的理由和证据，你可以修改、取消或补充；</li>
                    <li>数据库表通过已配置数据源读取，只注册选中的表，不会执行定时拉取；</li>
                    <li>通用事实数据集：需要 时间列 + 层级 + 指标；时间列属于中通主干，明确点击导入后才会写入事实表、生成时间聚合节点、按阈值形成分枝并物化 DNA 拓扑；</li>
                    <li>维表/主数据：只需列 + 主键，明确点击导入后写入维表供基座浏览/翻译，不生成事实聚合或螺旋节点；</li>
                    <li>基座只承载经过确认的语义过滤/翻译特征；“上半年、冬季、同比、类别甲”等语义由基座规则翻译，不等于把时间序列字段放进基座。</li>
                    <li>指标列一律以 Decimal 文本存储，禁用浮点，保证金额精度。</li>
                  </ul>
                }
              />
            </Col>
          </Row>
        </Card>
      )}

      {step === 1 && probe && (
        <>
          <Card
            title={`第二步：数据预览（${probe.sheet}，共 ${
              probe.row_count_estimated ? "约 " : ""
            }${probe.row_count} 行，表头模式：${
              probe.header_mode === "double" ? "双表头" : "单表头"
            }）`}
          >
            <Table
              size="small"
              rowKey={(_r, i) => String(i)}
              dataSource={probe.preview}
              columns={previewColumns}
              scroll={{ x: "max-content" }}
              pagination={false}
            />
          </Card>

          <Card
            title="第二步：AI / 经验库 / 规则字段分析与人工确认"
            extra={
              <Space size={4}>
                <Tag color="blue">时间 {roleStat.time}</Tag>
                <Tag color="green">层级 {roleStat.level}</Tag>
                <Tag color="gold">指标 {roleStat.metric}</Tag>
                <Tag color="purple">基座 {layerStat.base}</Tag>
                <Tag color="cyan">主干 {layerStat.trunk}</Tag>
                <Tag color="orange">分枝 {layerStat.branch}</Tag>
                <Tag>忽略 {layerStat.none}</Tag>
              </Space>
            }
          >
            <Alert
              type={recommendationStat.conflicts ? "warning" : "info"}
              showIcon
              message={`已完成字段分析：${probe.analysis_source || "规则回退"}；系统预勾选 ${recommendationStat.recommended} 个字段`}
              description={
                <div>
                  <div>系统依据：项目经验推荐库 + 当前字段样本画像 + AI 建议。基座/主干/分枝是通用语义层，不代表产品专用结构。</div>
                  <div style={{ marginTop: 4 }}>
                    {recommendationStat.conflicts
                      ? `发现 ${recommendationStat.conflicts} 个经验库与 AI 冲突字段，已保守标记，必须人工判断。`
                      : "没有发现经验库与 AI 的冲突字段；仍需由你确认字段是否适合本业务。"}
                  </div>
                </div>
              }
              style={{ marginBottom: 12 }}
            />
            {meta.kind === "generic" && (
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <div>
                  <span style={{ marginRight: 8 }}>自动派生时间层级：</span>
                  <Select
                    mode="multiple"
                    style={{ minWidth: 320 }}
                    value={derive}
                    onChange={setDerive}
                    options={Object.entries(DERIVE_META).map(([value, m]) => ({
                      value,
                      label: m.label,
                    }))}
                  />
                  <span style={{ marginLeft: 8, color: "#888", fontSize: 12 }}>
                    由时间列派生，无需额外数据列
                  </span>
                </div>
                <Alert
                  type="warning"
                  showIcon
                  message="层级顺序即下钻路径。某行列值为空时，该行不会被计入后续层级（截断语义），请把可空维度排在最后。"
                />
                <div>
                  <span style={{ marginRight: 8 }}>层级链顺序（下钻路径）：</span>
                  <Space size={4} wrap>
                    {derive.filter((d) => DERIVE_META[d]).map((d) => (
                      <Tag key={`derive-${d}`} color="blue">{DERIVE_META[d].label}</Tag>
                    ))}
                    {levelOrder.length === 0 && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        暂无维度层级
                      </Typography.Text>
                    )}
                    {levelOrder.map((key, index) => {
                      const col = cols.find((c) => c.levelKey === key && c.role === "level");
                      return (
                        <Tag key={key} color="geekblue" style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
                          {col?.label || key}
                          <Button
                            size="small"
                            type="text"
                            disabled={index <= 0}
                            onClick={() =>
                              setLevelOrder((prev) => {
                                const next = [...prev];
                                [next[index - 1], next[index]] = [next[index], next[index - 1]];
                                return next;
                              })
                            }
                          >
                            ↑
                          </Button>
                          <Button
                            size="small"
                            type="text"
                            disabled={index >= levelOrder.length - 1}
                            onClick={() =>
                              setLevelOrder((prev) => {
                                const next = [...prev];
                                [next[index + 1], next[index]] = [next[index], next[index + 1]];
                                return next;
                              })
                            }
                          >
                            ↓
                          </Button>
                        </Tag>
                      );
                    })}
                  </Space>
                  <div style={{ color: "#888", fontSize: 12, marginTop: 4 }}>
                    派生时间层固定在最前；上下按钮调整维度层级的下钻顺序（例如：组织编码 → 组织层级三 → 定位单元 → 商品）。季节、季度、上下半年、单月/双月和节气是可选标准时间标签，是否写入事实层由你确认。
                  </div>
                </div>
                {MONTH_GRANULARITY_FORMATS.includes(cols.find((c) => c.role === "time")?.timeFormat || "") && (
                  <Alert
                    type="error"
                    showIcon
                    message="当前时间列是月粒度，引擎只能精确到月"
                    description={
                      <div>
                        <div>
                          月粒度源文件里不存在“某一天/某一周”的记录，因此“上周”“昨天”“日均业务数量”这类问题无法精确回答，
                          系统会如实降级为月粒度而不是编造数据。
                        </div>
                        <div style={{ marginTop: 4 }}>
                          需要日粒度：请改用带具体日期的源文件（列值形如 20240131 或 2024-01-31），
                          在“物理列名 / 层级标识”列把时间格式选为 yyyy-mm-dd、yyyymmdd 或 d/m/yyyy，勾选“周”“日”派生层后即可支持周维度和环比/同比。
                        </div>
                      </div>
                    }
                  />
                )}
              </Space>
            )}
            <Space wrap style={{ marginBottom: 12 }}>
              <Input.Search
                placeholder="按列名 / 中文名筛选"
                allowClear
                style={{ width: 220 }}
                onChange={(e) => setQuery(e.target.value)}
              />
              <Checkbox
                checked={onlyActive}
                onChange={(e) => setOnlyActive(e.target.checked)}
              >
                只看已选用列
              </Checkbox>
              <Divider type="vertical" />
              <span style={{ fontSize: 12, color: "#888" }}>
                已勾选 {selected.length} 列：
              </span>
              <Button
                size="small"
                disabled={!selected.length}
                onClick={() => setRolesBulk(selected, "level")}
              >
                {meta.kind === "dimension" ? "设为维度列" : "设为层级"}
              </Button>
              {meta.kind === "generic" && (
                <>
                  <Button
                    size="small"
                    disabled={!selected.length}
                    onClick={() => setRolesBulk(selected, "metric")}
                  >
                    设为指标
                  </Button>
                  <Button
                    size="small"
                    disabled={!selected.length}
                    onClick={() => setRolesBulk(selected, "time")}
                  >
                    设为时间列
                  </Button>
                  <Button
                    size="small"
                    disabled={!selected.length}
                    onClick={() => setRolesBulk(selected, "fact")}
                  >
                    设为事实列
                  </Button>
                </>
              )}
              <Button
                size="small"
                disabled={!selected.length}
                onClick={() => setRolesBulk(selected, "ignore")}
              >
                设为忽略
              </Button>
              <Divider type="vertical" />
              <Button
                size="small"
                onClick={() => {
                  setRolesBulk(
                    cols.map((c) => c.index),
                    "ignore"
                  );
                  setSelected([]);
                }}
              >
                全部忽略
              </Button>
              <Button
                size="small"
                disabled={!probe}
                onClick={() => {
                  if (probe) setCols(toWizardColumns(probe));
                  setAnalysisConfirmed(false);
                  setSelected([]);
                }}
              >
                恢复建议角色
              </Button>
            </Space>
            <Table
              size="small"
              rowKey="index"
              dataSource={filteredCols}
              columns={colTableColumns}
              rowSelection={{
                selectedRowKeys: selected,
                onChange: (keys) => setSelected(keys as number[]),
                columnWidth: 36,
              }}
              pagination={{
                pageSize: 50,
                showSizeChanger: true,
                pageSizeOptions: [20, 50, 100, 200],
                showTotal: (t) => `共 ${t} 列`,
              }}
              scroll={{ x: "max-content" }}
            />
            <Card
              size="small"
              title="系统预勾选依据（请在这里完成判断后再继续）"
              style={{ marginTop: 16, background: "#fafafa" }}
            >
              <Space direction="vertical" size={6} style={{ width: "100%" }}>
                {cols.filter((c) => c.included).length === 0 && (
                  <span style={{ color: "#999" }}>当前没有纳入字段，请在上方勾选需要的字段。</span>
                )}
                {cols.filter((c) => c.included).map((c) => (
                  <div key={c.index}>
                    <Space size={4} wrap>
                      {normalizeFeatureLayers(c.featureLayers, c.featureLayer).map((layer) => (
                        <Tag key={layer} color={layer === "base" ? "purple" : layer === "trunk" ? "cyan" : "orange"}>
                          {FEATURE_LAYER_LABELS[layer]}
                        </Tag>
                      ))}
                    </Space>
                    <strong>{c.source}</strong>
                    <span style={{ marginLeft: 8, color: "#555" }}>
                      {c.role === "time" ? "时间" : c.role === "metric" ? "指标" : c.role === "fact" ? "事实列（不建层）" : "维度"}
                      {c.baseFeature ? " · 参与基座过滤/翻译" : ""}
                      {c.queryUses?.length ? ` · 用于${c.queryUses.map((u) => QUERY_USE_LABELS[u]).join("、")}` : ""}
                    </span>
                    <span style={{ marginLeft: 8, color: c.ai_conflict ? "#d46b08" : "#888" }}>
                      {c.suggestion_reason || "基于字段画像的通用规则建议"}
                    </span>
                  </div>
                ))}
              </Space>
            </Card>
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message="人工确认是导入前置条件"
              description="系统只负责给出预勾选和依据，不会替你决定。你可以修改纳入状态、角色、推荐层级、基座语义和查询用途；确认无误后勾选下面的确认框。"
            />
            <Checkbox
              checked={analysisConfirmed}
              onChange={(e) => setAnalysisConfirmed(e.target.checked)}
              style={{ marginTop: 12 }}
            >
              我已检查并确认字段分析建议（必要时已修改/补充），允许进入声明确认
            </Checkbox>
            {meta.kind === "dimension" && (
              <div style={{ marginTop: 12 }}>
                <span style={{ marginRight: 8 }}>主键列：</span>
                <Select
                  style={{ width: 260 }}
                  value={primaryKey}
                  onChange={setPrimaryKey}
                  options={cols
                    .filter((c) => c.role === "level")
                    .map((c) => ({ value: c.source, label: `${c.source} / ${c.label}` }))}
                />
              </div>
            )}
            <Space style={{ marginTop: 16 }}>
              <Button onClick={() => setStep(0)}>上一步</Button>
              <Button type="primary" disabled={!analysisConfirmed} onClick={() => setStep(2)}>
                下一步：确认声明（不导入）
              </Button>
            </Space>
          </Card>
        </>
      )}

      {step === 2 && (
        <Card title="第三步：确认声明（只注册，不导入）">
          <Descriptions size="small" column={3} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="数据集 id">{meta.id}</Descriptions.Item>
            <Descriptions.Item label="名称">{meta.label}</Descriptions.Item>
            <Descriptions.Item label="类型">
              {meta.kind === "generic" ? "通用事实数据集" : "维表/主数据"}
            </Descriptions.Item>
            <Descriptions.Item label="基座特征">{layerStat.base} 个</Descriptions.Item>
            <Descriptions.Item label="主干特征">{layerStat.trunk} 个</Descriptions.Item>
            <Descriptions.Item label="分枝特征">{layerStat.branch} 个</Descriptions.Item>
          </Descriptions>
          <Alert
            type="info"
            showIcon
            message="保存声明不会导入数据"
            description="这一步只把你确认后的字段角色、基座特征、时间入口和主干/分枝特征计划写入注册表，不写事实行、不生成聚合节点、不重建智慧树。物理数据和拓扑要等下一步明确点击开始导入。"
            style={{ marginBottom: 12 }}
          />
          <Collapse
            items={[
              {
                key: "decl",
                label: "查看将写入注册表的声明（可展开核对）",
                children: (
                  <pre style={{ maxHeight: 320, overflow: "auto", fontSize: 12 }}>
                    {JSON.stringify(draft, null, 2)}
                  </pre>
                ),
              },
            ]}
          />
          <Space style={{ marginTop: 16 }}>
            <Button onClick={() => setStep(1)}>上一步</Button>
            <Button type="primary" loading={busy} onClick={saveDeclaration}>
              保存确认后的声明
            </Button>
          </Space>
        </Card>
      )}

      {step === 3 && (
        <>
          <Card title="第四步：明确开始导入">
            <Alert
              type="warning"
              showIcon
              message={registered ? "数据集声明已保存，尚未导入" : "请先保存数据集声明"}
              description="只有点击下面的按钮才会读取 Excel/数据库表并写入数据集。通用事实导入会完成事实表、时间聚合、阈值分枝、双链拓扑和 JSON 物化；维表导入只更新维表，不生成螺旋节点。此按钮不会再次执行字段分析，也不会静默导入。"
              style={{ marginBottom: 12 }}
            />
            <Space style={{ marginBottom: 12 }}>
              <span>导入模式：</span>
              <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
                <Radio.Button value="replace">覆盖重导</Radio.Button>
                <Radio.Button value="dedupe">去重(幂等)</Radio.Button>
                <Radio.Button value="append">直接追加</Radio.Button>
              </Radio.Group>
            </Space>
            <div>
              <Button type="primary" loading={busy || running} disabled={!registered || running} onClick={startImport}>
                开始导入
              </Button>
              <Button style={{ marginLeft: 8 }} onClick={() => setStep(2)} disabled={running}>
                返回核对声明
              </Button>
            </div>
          </Card>
          {task && <ImportProgress task={task} percent={percent} />}
          {!running && task && (
            <Card>
              <Space>
                <Button type="primary" onClick={() => navigate("/datasets")}>
                  返回数据集列表
                </Button>
                <Button onClick={() => navigate("/agent")}>去 Agent 工作台验证</Button>
              </Space>
            </Card>
          )}
        </>
      )}
    </Space>
  );
}
