import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

type TreeSpec = {
  title: string;
  tint: string;
  branchLabels: string[];
  delay: number;
};

const trees: TreeSpec[] = [
  { title: '业务域乙明细', tint: '#55a6d8', branchLabels: ['服务单元', '品类'], delay: 0 },
  { title: '业务域甲日报', tint: '#2ca675', branchLabels: ['组织层级一', '组织层级二', '定位单元'], delay: 12 },
  { title: '业务域甲月报', tint: '#b85f43', branchLabels: ['区域', '产品类别'], delay: 24 },
  { title: '业务域甲计划', tint: '#8a6cc9', branchLabels: ['组织层级一', '产品类别'], delay: 36 },
];

const clamp = (value: number, from: number, to: number) => Math.min(Math.max(value, from), to);
const appear = (frame: number, start: number, duration = 18) =>
  interpolate(frame, [start, start + duration], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

const Arrow = ({ x1, y1, x2, y2, color = '#4e7ea6', width = 3, opacity = 1 }: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color?: string;
  width?: number;
  opacity?: number;
}) => (
  <g opacity={opacity}>
    <path d={`M ${x1} ${y1} L ${x2} ${y2}`} stroke={color} strokeWidth={width} fill="none" />
    <path d={`M ${x2 - 7} ${y2 + 10} L ${x2} ${y2} L ${x2 + 7} ${y2 + 10}`} stroke={color} strokeWidth={width} fill="none" />
  </g>
);

const LayerKey = () => (
  <div
    style={{
      position: 'absolute',
      left: 30,
      top: 115,
      width: 178,
      border: '1px solid #c9dcf2',
      borderRadius: 16,
      background: 'rgba(255,255,255,0.94)',
      padding: 12,
      color: '#31526f',
      fontSize: 15,
      lineHeight: 1.6,
      boxShadow: '0 8px 20px rgba(48,84,116,0.10)',
    }}
  >
    <strong>三层数据空间</strong>
    <div><span style={{ color: '#1570ef' }}>■</span> 中通内链：共有定位</div>
    <div><span style={{ color: '#18a779' }}>■</span> 中通周存：物化聚合</div>
    <div><span style={{ color: '#d98608' }}>■</span> DNA 外链：特征与分枝</div>
  </div>
);

const Tree = ({ tree, x, frame, focused }: { tree: TreeSpec; x: number; frame: number; focused: boolean }) => {
  const { fps } = useVideoConfig();
  const growth = spring({ frame: frame - 190 - tree.delay, fps, config: { damping: 22, mass: 0.8, stiffness: 110 } });
  const branchGrowth = appear(frame, 415 + tree.delay, 32);
  const outputGrowth = appear(frame, 670 + tree.delay, 18);
  const stemTop = 205;
  const stemBottom = 552;
  const visibleHeight = interpolate(clamp(growth, 0, 1), [0, 1], [12, stemBottom - stemTop]);
  const branchLevels = [315, 395, 475].slice(0, tree.branchLabels.length);
  const branchOpacity = focused ? 1 : 0.68;

  return (
    <g opacity={interpolate(clamp(growth, 0, 1), [0, 0.25, 1], [0, 0.8, 1])}>
      <text x={x} y={154} textAnchor="middle" fontSize={18} fontWeight={700} fill="#24465f">{tree.title}</text>
      <text x={x} y={177} textAnchor="middle" fontSize={12} fill="#7890a4">独立数据集智慧树</text>

      <rect x={x - 42} y={stemBottom - visibleHeight} width={84} height={visibleHeight} rx={42} fill="rgba(217,134,8,0.10)" stroke="#d98608" strokeWidth={3} />
      <rect x={x - 31} y={stemBottom - visibleHeight} width={62} height={visibleHeight} rx={31} fill="rgba(24,167,121,0.12)" stroke="#18a779" strokeWidth={3} />
      <rect x={x - 18} y={stemBottom - visibleHeight} width={36} height={visibleHeight} rx={18} fill="rgba(21,112,239,0.18)" stroke="#1570ef" strokeWidth={3} />

      {growth > 0.15 && (
        <>
          <text x={x} y={255} textAnchor="middle" fontSize={11} fill="#1570ef">中通内链</text>
          <text x={x + 50} y={278} fontSize={10} fill="#18a779">周存</text>
          <text x={x + 62} y={297} fontSize={10} fill="#d98608">外链</text>
        </>
      )}

      {branchLevels.map((y, index) => {
        const isLeft = index % 2 === 0;
        const direction = isLeft ? -1 : 1;
        const endX = x + direction * (92 + index * 10);
        const branchEndX = x + direction * (135 + index * 13);
        const endY = y - 38;
        const branchPath = `M ${x + direction * 17} ${y} C ${x + direction * 45} ${y}, ${x + direction * 60} ${endY}, ${endX} ${endY}`;
        const leafPath = `M ${endX} ${endY} C ${endX + direction * 24} ${endY - 10}, ${branchEndX} ${endY - 4}, ${branchEndX} ${endY - 4}`;
        const labelX = branchEndX + direction * 10;

        return (
          <g key={`${tree.title}-${y}`} opacity={branchGrowth * branchOpacity}>
            <path d={branchPath} stroke="#1570ef" strokeWidth={4} fill="none" />
            <path d={branchPath} stroke="#18a779" strokeWidth={7} opacity={0.45} fill="none" />
            <path d={leafPath} stroke={tree.tint} strokeWidth={10} opacity={0.58} fill="none" strokeLinecap="round" />
            <rect x={branchEndX - 7} y={endY - 11} width={14} height={14} rx={3} fill={tree.tint} stroke="#ffffff" strokeWidth={2} />
            <text x={labelX} y={endY - 18} textAnchor={isLeft ? 'end' : 'start'} fontSize={11} fill="#547087">{tree.branchLabels[index]}</text>
          </g>
        );
      })}

      <g opacity={outputGrowth}>
        <rect x={x - 83} y={89} width={166} height={34} rx={8} fill="#fbf7ff" stroke="#8e5de7" strokeWidth={2} />
        <text x={x} y={111} textAnchor="middle" fontSize={13} fill="#5f3a9e" fontWeight={700}>{tree.title} · 结果</text>
        <Arrow x1={x} y1={stemTop - 8} x2={x} y2={123} color="#8e5de7" width={2} opacity={outputGrowth} />
      </g>
    </g>
  );
};

export const WisdomTreeQueryExplainer = () => {
  const frame = useCurrentFrame();
  const inputOpacity = appear(frame, 10, 20);
  const baseOpacity = appear(frame, 80, 24);
  const allTreesOpacity = appear(frame, 180, 30);
  const flowOpacity = appear(frame, 365, 20);
  const mergeOpacity = appear(frame, 800, 24);
  const finalOpacity = appear(frame, 870, 20);
  const titleOpacity = 1 - interpolate(frame, [70, 120], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  return (
    <AbsoluteFill style={{ background: 'linear-gradient(180deg, #f8fbff 0%, #eff7ff 100%)', fontFamily: 'Microsoft YaHei, Segoe UI, sans-serif', color: '#173a56' }}>
      <svg width="1280" height="720" viewBox="0 0 1280 720">
        <defs>
          <filter id="shadow"><feDropShadow dx="0" dy="4" stdDeviation="6" floodColor="#27455f" floodOpacity="0.16" /></filter>
        </defs>
        <g opacity={titleOpacity}>
          <text x="640" y="170" textAnchor="middle" fontSize="42" fontWeight="700" fill="#173a56">AI 数据引擎智慧树</text>
          <text x="640" y="210" textAnchor="middle" fontSize="20" fill="#547087">从一个自然语言问题到可信的统一结果</text>
        </g>

        <g opacity={inputOpacity} filter="url(#shadow)">
          <rect x="492" y="590" width="296" height="56" rx="12" fill="#ffffff" stroke="#377fc5" strokeWidth="3" />
          <text x="640" y="614" textAnchor="middle" fontSize="17" fontWeight="700" fill="#1f5f9d">用户 / Agent 问题入口</text>
          <text x="640" y="635" textAnchor="middle" fontSize="13" fill="#63829a">例如：2026 年 6 月第一周各组织层级一业务数据甲数量</text>
        </g>

        <g opacity={baseOpacity} filter="url(#shadow)">
          <Arrow x1={640} y1={590} x2={640} y2={560} color="#377fc5" />
          <rect x="130" y="525" width="1020" height="54" rx="14" fill="#fff6e5" stroke="#8b5e34" strokeWidth="3" />
          <text x="640" y="549" textAnchor="middle" fontSize="18" fontWeight="700" fill="#75431e">统一共享基座 · 问题分解规范器</text>
          <text x="640" y="569" textAnchor="middle" fontSize="12" fill="#956a45">过滤 · 翻译 · 赋能 · 纠偏，把人类问题编译为树可执行的标准时间和业务参数</text>
        </g>

        <g opacity={flowOpacity}>
          <rect x="237" y="479" width="806" height="28" rx="8" fill="rgba(255,255,255,0.72)" stroke="#c9dcf2" />
          <text x="640" y="498" textAnchor="middle" fontSize="13" fontWeight="700" fill="#416580">中通内链进 → 中通周存过 → DNA 外链出</text>
        </g>

        <g opacity={allTreesOpacity}>
          {trees.map((tree, index) => <Tree key={tree.title} tree={tree} x={255 + index * 258} frame={frame} focused={frame > 390 && frame < 690} />)}
        </g>

        <g opacity={mergeOpacity} filter="url(#shadow)">
          <path d="M 255 88 L 255 79 L 1025 79 L 1025 88" fill="none" stroke="#8e5de7" strokeWidth="3" />
          <rect x="506" y="43" width="268" height="30" rx="8" fill="#f8f0ff" stroke="#8e5de7" strokeWidth="2" />
          <text x="640" y="63" textAnchor="middle" fontSize="14" fontWeight="700" fill="#5f3a9e">合并 / 汇总节点</text>
        </g>

        <g opacity={finalOpacity} filter="url(#shadow)">
          <Arrow x1={640} y1={43} x2={640} y2={38} color="#8e5de7" width={2} />
          <rect x="488" y="4" width="304" height="30" rx="8" fill="#efeaff" stroke="#6d47bd" strokeWidth="3" />
          <text x="640" y="24" textAnchor="middle" fontSize="15" fontWeight="700" fill="#4c2e87">统一结果输出：表格、图表、解释</text>
        </g>
      </svg>

      <LayerKey />
      <div style={{ position: 'absolute', right: 30, bottom: 18, color: '#5e7d94', fontSize: 13, opacity: Math.max(flowOpacity, 0.25) }}>
        拓扑演示数据，不含真实业务记录
      </div>
    </AbsoluteFill>
  );
};
