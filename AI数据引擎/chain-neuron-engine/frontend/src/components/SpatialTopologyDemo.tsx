import {
  ApartmentOutlined,
  DatabaseOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { errorMessage } from "../api/client";
import { getSpiralTopology, getSpiralTopologySlice, listDatasets } from "../api/endpoints";
import type {
  DatasetRouteView,
  DatasetSummary,
  RouteInfo,
  RouteStep,
  SpiralTopologyNode,
  SpiralTopologySummary,
} from "../api/types";
import "./SpatialTopologyDemo.css";

const { Text } = Typography;

const TREE_COLORS = ["#2878b5", "#6f50a7", "#11835a", "#b05a45", "#176b87"];
const INNER = "#2678d8";
const STORE = "#1b9a6c";
const OUTER = "#d88400";
const DEFAULT_QUERY_PATH = ["基座规范", "中通内链", "中通周存", "DNA 外链", "事实结果"];

interface DatasetTopologyState {
  dataset: DatasetSummary;
  nodes: SpiralTopologyNode[];
  summary: SpiralTopologySummary | null;
  error: string | null;
}

interface PositionedNode extends SpiralTopologyNode {
  x: number;
  y: number;
  parentX: number;
  parentY: number;
  highlighted: boolean;
}

interface TreeLayout {
  nodes: PositionedNode[];
  byId: Map<string, PositionedNode>;
  trunk: PositionedNode[];
  branches: PositionedNode[];
  branchTotal: number;
}

type QueryStatus = "hit" | "empty" | "idle";

function compact(value: string | null | undefined, max = 18): string {
  const text = String(value || "-");
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

function formatNumber(value: number | null | undefined): string {
  return value == null ? "-" : new Intl.NumberFormat("zh-CN").format(value);
}

function temporalLayer(node: SpiralTopologyNode): number {
  return node.topology.temporal_layer ?? node.topology.helix_layer ?? node.level;
}

function hashSide(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = ((hash << 5) - hash) + value.charCodeAt(index);
  return Math.abs(hash) % 2 === 0 ? -1 : 1;
}

function representativeBranches(
  nodes: SpiralTopologyNode[],
  highlighted: Set<string>,
  limit = 18,
): SpiralTopologyNode[] {
  if (nodes.length <= limit) return nodes;
  const byId = new Map(nodes.map((node) => [node.node_id, node]));
  const selected = new Set<string>();

  const addChain = (candidate: SpiralTopologyNode) => {
    const chain: SpiralTopologyNode[] = [];
    let current: SpiralTopologyNode | undefined = candidate;
    const visited = new Set<string>();
    while (current && !visited.has(current.node_id)) {
      visited.add(current.node_id);
      chain.push(current);
      const parentId = current.topology.growth_parent_id || current.parent_id || "";
      current = byId.get(parentId);
    }
    chain.reverse().forEach((item) => selected.add(item.node_id));
  };

  nodes
    .filter((node) => highlighted.has(node.node_id))
    .slice(0, limit)
    .forEach(addChain);

  const byLayer = new Map<number, SpiralTopologyNode[]>();
  for (const node of nodes) {
    const layer = temporalLayer(node);
    const items = byLayer.get(layer) || [];
    items.push(node);
    byLayer.set(layer, items);
  }
  const groups = [...byLayer.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, items]) => items.sort((a, b) => (
      (b.topology.branch_depth || 0) - (a.topology.branch_depth || 0)
      || (b.record_count || 0) - (a.record_count || 0)
      || a.node_id.localeCompare(b.node_id)
    )));

  let cursor = 0;
  let added = true;
  while (selected.size < limit && added) {
    added = false;
    for (const group of groups) {
      const candidate = group[cursor];
      if (!candidate || selected.has(candidate.node_id)) continue;
      const before = selected.size;
      addChain(candidate);
      if (selected.size > limit) {
        const newlyAdded = [...selected].slice(before);
        newlyAdded.forEach((nodeId) => selected.delete(nodeId));
        continue;
      }
      added = selected.size > before || added;
      if (selected.size >= limit) break;
    }
    cursor += 1;
  }

  return nodes.filter((node) => selected.has(node.node_id));
}

function queryStatus(overlay: DatasetRouteView | null, active: boolean): QueryStatus {
  if (!active || !overlay) return "idle";
  return overlay.available ? "hit" : "empty";
}

function statusLabel(status: QueryStatus, overlay: DatasetRouteView | null): string {
  if (status === "hit") return `命中 ${formatNumber(overlay?.record_count || 0)} 条`;
  if (status === "empty") return "当前范围 0 条，合并时跳过";
  return "等待查询";
}

function routeNodeIds(nodes: SpiralTopologyNode[], overlay: DatasetRouteView | null): Set<string> {
  if (!overlay) return new Set();
  const explicit = new Set([
    ...(overlay.route?.target_node_ids || []),
    ...(overlay.route?.inner_node_ids || []),
    ...(overlay.route?.middle_store_node_ids || []),
  ]);
  const values = new Set(
    (overlay.steps || [])
      .filter((step) => step.status !== "pruned")
      .map((step) => String(step.value)),
  );
  const rawById = new Map<string, SpiralTopologyNode>(nodes.map((node) => [node.node_id, node]));
  const result = new Set<string>();
  for (const node of nodes) {
    if (!explicit.has(node.node_id) && !values.has(String(node.value))) continue;
    let current: SpiralTopologyNode | undefined = node;
    const visited = new Set<string>();
    while (current && !visited.has(current.node_id)) {
      visited.add(current.node_id);
      result.add(current.node_id);
      const parentId: string | null = current.topology.growth_parent_id || current.parent_id;
      current = parentId ? rawById.get(parentId) : undefined;
    }
  }
  return result;
}

function makeTreeLayout(
  state: DatasetTopologyState,
  centerX: number,
  slotWidth: number,
  baseY: number,
  outputY: number,
  overlay: DatasetRouteView | null,
): TreeLayout {
  const highlighted = routeNodeIds(state.nodes, overlay);
  const trunks = state.nodes
    .filter((node) => node.topology.role === "trunk")
    .sort((a, b) => temporalLayer(a) - temporalLayer(b) || a.level - b.level || a.node_id.localeCompare(b.node_id));
  const allBranchNodes = state.nodes
    .filter((node) => node.topology.role === "branch" || (node.topology.branch_depth || 0) > 0)
    .sort((a, b) => (a.topology.branch_depth || 0) - (b.topology.branch_depth || 0)
      || temporalLayer(a) - temporalLayer(b)
      || a.node_id.localeCompare(b.node_id));
  const branchNodes = representativeBranches(allBranchNodes, highlighted);
  const byId = new Map<string, PositionedNode>();
  const positionedTrunks: PositionedNode[] = [];
  const trunkTop = outputY + 96;
  const trunkBottom = baseY - 42;
  const trunkSpan = Math.max(1, trunks.length - 1);
  trunks.forEach((node, index) => {
    const y = trunkBottom - ((trunkBottom - trunkTop) * index / trunkSpan);
    const positioned: PositionedNode = {
      ...node,
      x: centerX,
      y,
      parentX: centerX,
      parentY: index === 0 ? baseY : positionedTrunks[index - 1].y,
      highlighted: highlighted.has(node.node_id),
    };
    positionedTrunks.push(positioned);
    byId.set(node.node_id, positioned);
  });

  const siblingCounters = new Map<string, number>();
  const positionedBranches: PositionedNode[] = [];
  branchNodes.forEach((node) => {
    const parentId = node.topology.growth_parent_id || node.parent_id || "";
    let parent = byId.get(parentId);
    if (!parent && positionedTrunks.length) {
      parent = positionedTrunks.reduce((nearest, candidate) => (
        Math.abs(temporalLayer(candidate) - temporalLayer(node))
          < Math.abs(temporalLayer(nearest) - temporalLayer(node)) ? candidate : nearest
      ));
    }
    const parentX = parent?.x ?? centerX;
    const parentY = parent?.y ?? (baseY - 90);
    const siblingIndex = siblingCounters.get(parentId) || 0;
    siblingCounters.set(parentId, siblingIndex + 1);
    const depth = Math.max(1, node.topology.branch_depth || 1);
    const parentIsBranch = Boolean(parent && (parent.topology.role === "branch" || (parent.topology.branch_depth || 0) > 0));
    const inheritedSide = parentIsBranch && parent
      ? (parent.x < centerX ? -1 : 1)
      : hashSide(parentId || node.node_id) * (siblingIndex % 2 === 0 ? 1 : -1);
    const fanIndex = Math.floor(siblingIndex / 2);
    const currentOffset = parentIsBranch && parent ? Math.abs(parent.x - centerX) : 0;
    const maxOffset = Math.max(72, slotWidth / 2 - 28);
    const targetOffset = parentIsBranch
      ? currentOffset + 30 + fanIndex * 6
      : 58 + fanIndex * 16;
    const x = centerX + inheritedSide * Math.min(maxOffset, targetOffset);
    const yStep = parentIsBranch ? 31 : 35;
    const y = Math.max(outputY + 72, parentY - yStep - fanIndex * 11 - Math.max(0, depth - 1) * 3);
    const positioned: PositionedNode = {
      ...node,
      x,
      y,
      parentX,
      parentY,
      highlighted: highlighted.has(node.node_id),
    };
    positionedBranches.push(positioned);
    byId.set(node.node_id, positioned);
  });

  return {
    nodes: [...positionedTrunks, ...positionedBranches],
    byId,
    trunk: positionedTrunks,
    branches: positionedBranches,
    branchTotal: state.summary?.branch_nodes ?? allBranchNodes.length,
  };
}

function TubePath({ d, active = false, branch = false }: { d: string; active?: boolean; branch?: boolean }) {
  const scale = branch ? 0.36 : 1;
  return (
    <g className={active ? "wisdom-path active" : "wisdom-path"}>
      {active && (
        <path d={d} fill="none" stroke="#7549bf" strokeWidth={48 * scale} strokeLinecap="round" strokeLinejoin="round" opacity="0.18" />
      )}
      <path d={d} fill="none" stroke={OUTER} strokeWidth={40 * scale} strokeLinecap="round" strokeLinejoin="round" opacity={active ? 0.9 : 0.62} />
      <path d={d} fill="none" stroke="#fff8e8" strokeWidth={30 * scale} strokeLinecap="round" strokeLinejoin="round" />
      <path d={d} fill="none" stroke={STORE} strokeWidth={27 * scale} strokeLinecap="round" strokeLinejoin="round" opacity={active ? 0.96 : 0.74} />
      <path d={d} fill="none" stroke="#effcf6" strokeWidth={17 * scale} strokeLinecap="round" strokeLinejoin="round" />
      <path d={d} fill="none" stroke={INNER} strokeWidth={14 * scale} strokeLinecap="round" strokeLinejoin="round" opacity={active ? 1 : 0.86} />
    </g>
  );
}

function DatasetTree({
  state,
  centerX,
  slotWidth,
  baseY,
  outputY,
  overlay,
  queryActive,
  color,
  onSelect,
}: {
  state: DatasetTopologyState;
  centerX: number;
  slotWidth: number;
  baseY: number;
  outputY: number;
  overlay: DatasetRouteView | null;
  queryActive: boolean;
  color: string;
  onSelect: (dataset: string, node: SpiralTopologyNode) => void;
}) {
  const layout = makeTreeLayout(state, centerX, slotWidth, baseY, outputY, overlay);
  const status = queryStatus(overlay, queryActive);
  const trunkPath = `M ${centerX} ${baseY} C ${centerX - 8} ${baseY - 145}, ${centerX + 8} ${outputY + 165}, ${centerX} ${outputY + 52}`;
  const summary = state.summary;
  const threshold = summary?.growth_threshold || state.dataset.structure?.growth?.threshold;

  return (
    <g className={`wisdom-tree ${status}`}>
      <line x1={centerX} y1={outputY + 27} x2={centerX} y2="77" stroke={status === "hit" ? "#7549bf" : "#b7a9c7"} strokeWidth="2" markerEnd="url(#flowArrow)" />
      <rect x={centerX - 82} y={outputY} width="164" height="30" rx="5" fill={status === "hit" ? "#f4eaff" : "#faf8fc"} stroke={status === "hit" ? "#8d5bd1" : "#c9bfd2"} />
      <text x={centerX} y={outputY + 13} textAnchor="middle" fill="#49345f" fontSize="10" fontWeight="700">{compact(state.dataset.label, 14)} · output</text>
      <text x={centerX} y={outputY + 24} textAnchor="middle" fill="#756683" fontSize="8">{statusLabel(status, overlay)}</text>

      {layout.branches.map((node) => {
        const direction = node.x >= node.parentX ? 1 : -1;
        const controlOne = node.parentX + direction * 18;
        const controlTwo = node.x - direction * 20;
        const d = `M ${node.parentX} ${node.parentY} C ${controlOne} ${node.parentY - 3}, ${controlTwo} ${node.y + 12}, ${node.x} ${node.y}`;
        return <TubePath key={`branch-${node.node_id}`} d={d} active={node.highlighted} branch />;
      })}

      <TubePath d={trunkPath} active={status === "hit"} />

      {layout.trunk.map((node) => (
        <g key={node.node_id} className="wisdom-node" onClick={() => onSelect(state.dataset.id, node)}>
          <rect x={node.x - 6} y={node.y - 5} width="12" height="10" rx="2" fill={node.highlighted ? "#ffd666" : INNER} stroke="#fff" strokeWidth="1.5" />
          <title>{`${state.dataset.label}\n公共时间主干：${node.level_name}=${node.value}\n${formatNumber(node.record_count)} 条`}</title>
        </g>
      ))}

      {layout.branches.map((node, index) => (
        <g key={node.node_id} className="wisdom-node" onClick={() => onSelect(state.dataset.id, node)}>
          <rect x={node.x - 7} y={node.y - 6} width="14" height="12" rx="2" fill={node.highlighted ? "#ffd666" : color} stroke="#fff" strokeWidth="1.5" />
          {(index < 6 || node.highlighted) && (
            <text x={node.x + (node.x < centerX ? -10 : 10)} y={node.y - 8} textAnchor={node.x < centerX ? "end" : "start"} fill="#3c4f61" fontSize="8" fontWeight={node.highlighted ? "700" : "400"}>
              {compact(`${node.topology.growth_dimension || node.level_name}=${node.value}`, 20)}
            </text>
          )}
          <title>{`${state.dataset.label}\n真实分枝 ${node.topology.branch_depth || 1}：${node.topology.growth_dimension || node.level_name}=${node.value}\n父节点：${node.topology.growth_parent_id || node.parent_id || "-"}\n${formatNumber(node.record_count)} 条`}</title>
        </g>
      ))}

      <text x={centerX} y={baseY - 16} textAnchor="middle" fill="#263f57" fontSize="11" fontWeight="700">{state.dataset.label}</text>
      <text x={centerX} y={baseY - 4} textAnchor="middle" fill="#6c7b8b" fontSize="8">
        {formatNumber(summary?.node_count || state.nodes.length)} 节点 · 主干 {summary?.trunk_nodes ?? layout.trunk.length} · 分枝 {formatNumber(layout.branchTotal)}
      </text>
      {layout.branchTotal > layout.branches.length && (
        <text x={centerX} y={baseY + 12} textAnchor="middle" fill="#8794a1" fontSize="7">
          当前图示 {layout.branches.length} 个跨时间圈代表分枝，点击节点查看真实属性
        </text>
      )}
      {!layout.branches.length && (
        <text x={centerX} y={baseY - 72} textAnchor="middle" fill="#9a6b1c" fontSize="8">
          暂无已物化分枝{threshold ? `（阈值 ${formatNumber(threshold)}）` : ""}
        </text>
      )}
      {state.error && <text x={centerX} y={baseY - 88} textAnchor="middle" fill="#b42318" fontSize="8">拓扑读取失败</text>}
      <line x1={centerX - slotWidth / 2 + 18} y1={baseY + 2} x2={centerX + slotWidth / 2 - 18} y2={baseY + 2} stroke="#d8c8b0" strokeWidth="1" strokeDasharray="4 4" />
    </g>
  );
}

function CrossSectionLegend({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="-55" y="-49" width="110" height="100" rx="6" fill="#fff" stroke="#d8e0e8" />
      <text x="0" y="-34" textAnchor="middle" fontSize="9" fill="#44576b" fontWeight="700">管状截面</text>
      <circle cx="0" cy="0" r="27" fill="#fff8e8" stroke={OUTER} strokeWidth="7" />
      <circle cx="0" cy="0" r="18" fill="#effcf6" stroke={STORE} strokeWidth="6" />
      <circle cx="0" cy="0" r="8" fill="#dcebff" stroke={INNER} strokeWidth="3" />
      <text x="0" y="45" textAnchor="middle" fontSize="7" fill="#6f7f90">内链 · 周存 · 外链</text>
    </g>
  );
}

function ForestCanvas({
  states,
  foundations,
  overlays,
  queryActive,
  onSelect,
}: {
  states: DatasetTopologyState[];
  foundations: DatasetTopologyState[];
  overlays: DatasetRouteView[];
  queryActive: boolean;
  onSelect: (dataset: string, node: SpiralTopologyNode) => void;
}) {
  const slotWidth = 340;
  const width = Math.max(1120, states.length * slotWidth + 170);
  const height = 850;
  const baseY = 700;
  const outputY = 98;
  const startX = (width - states.length * slotWidth) / 2 + slotWidth / 2;
  const baseLabels = foundations.map((state) => state.dataset.label).join("、") || "已确认基座特征与时间规则";

  return (
    <div className="wisdom-canvas-scroll">
      <svg className="wisdom-canvas" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="AI 数据引擎统一智慧树森林">
        <defs>
          <marker id="flowArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" fill="#7d55b3" />
          </marker>
        </defs>

        <rect x={width / 2 - 130} y="8" width="260" height="32" rx="5" fill="#efe5fb" stroke="#8d5bd1" />
        <text x={width / 2} y="21" textAnchor="middle" fontSize="11" fontWeight="700" fill="#51326f">统一结果输出</text>
        <text x={width / 2} y="33" textAnchor="middle" fontSize="8" fill="#765f88">总量按问题相加；分项保留各数据集口径；0 结果跳过</text>
        <line x1={width / 2} y1="77" x2={width / 2} y2="40" stroke="#7d55b3" strokeWidth="2" markerEnd="url(#flowArrow)" />
        <rect x={width / 2 - 92} y="50" width="184" height="27" rx="5" fill="#fbf7ff" stroke="#b494d4" />
        <text x={width / 2} y="67" textAnchor="middle" fontSize="10" fontWeight="700" fill="#67458a">合并 / 汇总节点</text>
        {states.length > 1 && (
          <line x1={startX} y1="77" x2={startX + (states.length - 1) * slotWidth} y2="77" stroke="#b7a9c7" strokeWidth="2" />
        )}

        {states.map((state, index) => {
          const centerX = startX + index * slotWidth;
          return (
            <DatasetTree
              key={state.dataset.id}
              state={state}
              centerX={centerX}
              slotWidth={slotWidth}
              baseY={baseY}
              outputY={outputY}
              overlay={overlays.find((item) => item.dataset === state.dataset.id) || null}
              queryActive={queryActive}
              color={TREE_COLORS[index % TREE_COLORS.length]}
              onSelect={onSelect}
            />
          );
        })}

        <rect x="46" y={baseY} width={width - 92} height="82" rx="5" fill="#f6ead5" stroke="#8a6137" strokeWidth="3" />
        <rect x="61" y={baseY + 14} width={width - 122} height="53" rx="3" fill="#fff9ed" stroke="#c9a776" />
        <text x={width / 2} y={baseY + 35} textAnchor="middle" fontSize="13" fontWeight="700" fill="#66451f">统一共享基座 · 问题分解规范器</text>
        <text x={width / 2} y={baseY + 51} textAnchor="middle" fontSize="9" fill="#7a684f">过滤 · 翻译 · 纠偏 · 赋能，将用户问题转换为智慧树可执行的标准时间和业务参数</text>
        <text x={width / 2} y={baseY + 64} textAnchor="middle" fontSize="8" fill="#927858">基座数据：{compact(baseLabels, 80)}</text>

        <line x1={width / 2} y1="806" x2={width / 2} y2={baseY + 82} stroke="#2878b5" strokeWidth="3" markerEnd="url(#flowArrow)" />
        <rect x={width / 2 - 120} y="806" width="240" height="32" rx="5" fill="#eaf4ff" stroke="#2878b5" />
        <text x={width / 2} y="820" textAnchor="middle" fontSize="10" fontWeight="700" fill="#1e5e91">input · 数据导入 / 用户与 Agent 调用</text>
        <text x={width / 2} y="832" textAnchor="middle" fontSize="8" fill="#56758d">从地面进入基座，经智慧树运算后向上汇聚输出</text>

        <CrossSectionLegend x={78} y={125} />
      </svg>
    </div>
  );
}

export default function SpatialTopologyDemo({
  route,
  steps = [],
  datasetViews = [],
}: {
  dataset?: string;
  route?: RouteInfo | null;
  steps?: RouteStep[];
  datasetViews?: DatasetRouteView[];
}) {
  const [datasets, setDatasets] = useState<DatasetTopologyState[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ dataset: string; node: SpiralTopologyNode } | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const registry = await listDatasets();
      const active = (registry.items || []).filter((item) => !item.disabled);
      const states = await Promise.all(active.map(async (dataset): Promise<DatasetTopologyState> => {
        if (dataset.kind === "dimension" || dataset.origin === "base" || dataset.base?.enabled) {
          return { dataset, nodes: [], summary: null, error: null };
        }
        try {
          const [slice, summary] = await Promise.all([
            getSpiralTopologySlice(dataset.id, 120),
            getSpiralTopology(dataset.id),
          ]);
          return { dataset, nodes: slice.items, summary, error: null };
        } catch (reason) {
          return { dataset, nodes: [], summary: null, error: errorMessage(reason, "读取真实拓扑失败") };
        }
      }));
      setDatasets(states);
    } catch (reason) {
      setDatasets([]);
      setError(errorMessage(reason, "读取已注册数据集失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadAll(); }, [loadAll]);

  const overlays = useMemo<DatasetRouteView[]>(() => {
    if (datasetViews.length) return datasetViews;
    if (!route) return [];
    return [{ dataset: route.dataset, label: route.dataset, available: true, route, steps: route.steps || steps }];
  }, [datasetViews, route, steps]);
  const queryActive = overlays.length > 0 || steps.length > 0;
  const foundations = datasets.filter((state) => state.dataset.kind === "dimension" || state.dataset.origin === "base" || state.dataset.base?.enabled);
  const foundationIds = new Set(foundations.map((state) => state.dataset.id));
  const trees = datasets.filter((state) => !foundationIds.has(state.dataset.id));
  const queryPath = overlays.find((item) => item.route?.query_path?.length)?.route?.query_path || DEFAULT_QUERY_PATH;

  return (
    <div className="wisdom-topology">
      <div className="wisdom-toolbar">
        <Space size={7} wrap>
          <Badge status={loading ? "processing" : queryActive ? "processing" : "success"} />
          <Text strong>{queryActive ? "统一智慧树森林 · 查询路径高亮" : "统一智慧树森林 · 结构总览"}</Text>
          <Tag color="blue">{trees.length} 棵数据集树</Tag>
          <Tag color="green">真实分枝 {formatNumber(trees.reduce((sum, item) => sum + (item.summary?.branch_nodes || 0), 0))}</Tag>
        </Space>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void loadAll()}>刷新</Button>
      </div>

      <div className="wisdom-route-strip">
        <span><DatabaseOutlined /> 统一基座</span>
        {queryPath.map((item, index) => (
          <span key={`${item}-${index}`} className={queryActive ? "active" : ""}>{compact(item.replace("foundation", "基座").replace("middle_inner", "中通内链").replace("middle_store", "中通周存").replace("outer_feature", "DNA 外链").replace("fact_result", "事实结果"), 14)}</span>
        ))}
        <span><ApartmentOutlined /> 统一输出</span>
      </div>

      {error && <Alert type="warning" showIcon message={error} />}
      {loading && !datasets.length ? (
        <div className="wisdom-loading"><Spin /><Text type="secondary">正在读取真实智慧树拓扑…</Text></div>
      ) : !trees.length ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无启用的业务数据集" />
      ) : (
        <ForestCanvas states={trees} foundations={foundations} overlays={overlays} queryActive={queryActive} onSelect={(dataset, node) => setSelected({ dataset, node })} />
      )}

      <div className="wisdom-legend">
        <span><i style={{ background: INNER }} />中心实心柱：中通 DNA 内链 / 公共时间特征</span>
        <span><i style={{ background: STORE }} />中间圆环柱：中通周存 / 聚合数据块</span>
        <span><i style={{ background: OUTER }} />外层圆环柱：DNA 外链 / 非共有特征</span>
        <span>侧向三层管道：真实递归分枝</span>
        <span>小方块：枝内物理数据块，不是独立查询层</span>
      </div>

      <Drawer title="真实节点说明" width={470} open={Boolean(selected)} onClose={() => setSelected(null)}>
        {selected && (
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="所属数据集">{selected.dataset}</Descriptions.Item>
            <Descriptions.Item label="角色"><Tag color={selected.node.topology.role === "branch" ? "green" : "blue"}>{selected.node.topology.role === "branch" ? "递归分枝" : "公共时间主干"}</Tag></Descriptions.Item>
            <Descriptions.Item label="字段和值">{selected.node.level_name} = {selected.node.value}</Descriptions.Item>
            <Descriptions.Item label="记录数">{formatNumber(selected.node.record_count)}</Descriptions.Item>
            <Descriptions.Item label="时间圈">{selected.node.topology.time_bucket || "-"} / 第 {selected.node.topology.temporal_layer ?? "-"} 圈</Descriptions.Item>
            <Descriptions.Item label="生长位置">深度 {selected.node.topology.branch_depth || 0} · {selected.node.topology.growth_dimension || "公共时间链"}</Descriptions.Item>
            <Descriptions.Item label="真实父节点"><Text copyable={{ text: selected.node.topology.growth_parent_id || selected.node.parent_id || "" }}>{compact(selected.node.topology.growth_parent_id || selected.node.parent_id, 42)}</Text></Descriptions.Item>
            <Descriptions.Item label="节点 ID"><Text copyable={{ text: selected.node.node_id }}>{compact(selected.node.node_id, 46)}</Text></Descriptions.Item>
            <Descriptions.Item label="三层空间">中通内链进 → 中通周存过 → DNA 外链出；DNA 仅表达物理连接，不增加查询层级。</Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
}
