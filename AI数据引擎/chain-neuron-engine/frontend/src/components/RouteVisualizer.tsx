import { BranchesOutlined, ScissorOutlined } from "@ant-design/icons";
import { Alert, Card, Empty, Space, Statistic, Steps, Tag, Typography } from "antd";

import type { RouteInfo, RouteStep } from "../api/types";

const { Text } = Typography;

const PATH_LABELS: Record<string, string> = {
  foundation: "基座过滤 / 翻译 / 纠偏",
  middle_inner: "中通内链进：时间圈剪枝",
  middle_store: "中通周存过：读取时间层聚合",
  outer_feature: "外链出：稀疏特征定位",
  fact_result: "事实表精确计算 / 返回结果",
};

const STATUS_MAP: Record<
  string,
  { status: "wait" | "process" | "finish" | "error"; color: string; label: string }
> = {
  active: { status: "process", color: "blue", label: "进入" },
  pruned: { status: "error", color: "default", label: "剪枝" },
  rollup: { status: "finish", color: "green", label: "直读" },
  hit: { status: "finish", color: "green", label: "命中" },
};

function StepTitle({ step }: { step: RouteStep }) {
  const meta = STATUS_MAP[step.status] || STATUS_MAP.active;
  return (
    <Space size={6}>
      <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>
        {step.level_label || step.level_name}
      </Tag>
      <Text strong>{step.value}</Text>
    </Space>
  );
}

export default function RouteVisualizer({
  steps,
  route,
}: {
  steps: RouteStep[];
  route?: RouteInfo | null;
}) {
  if (!steps?.length) {
    return <Empty description="本次回答未产生路由路径" />;
  }

  const queryPath = route?.query_path?.length
    ? route.query_path
    : ["foundation", "middle_inner", "middle_store", "outer_feature", "fact_result"];

  // 去重连续重复的展示（分组聚合时同层步骤多）
  const items = steps.map((s) => ({
    title: <StepTitle step={s} />,
    status: (STATUS_MAP[s.status] || STATUS_MAP.active).status,
    description: s.message ? <Text type="secondary">{s.message}</Text> : null,
  }));

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Card size="small" style={{ background: "#f8fbff" }}>
        <Space wrap size={[6, 6]} style={{ marginBottom: 8 }}>
          <Text strong>物理结构流</Text>
          {[
            ["数据输入", "blue"],
            ["统一基座", "gold"],
            ["各数据集智慧树", "cyan"],
            ["各自 output", "purple"],
            ["合并 / 汇总", "purple"],
            ["统一 output", "magenta"],
          ].map(([label, color], index, items) => (
            <span key={label}>
              <Tag color={color}>{label}</Tag>
              {index < items.length - 1 && <Text type="secondary"> → </Text>}
            </span>
          ))}
        </Space>
        <Space wrap size={[6, 6]}>
          <Text strong>查询路径</Text>
          {queryPath.map((key, index) => (
            <span key={`${key}-${index}`}>
              <Tag color={key === "middle_inner" ? "blue" : key === "middle_store" ? "cyan" : key === "outer_feature" ? "green" : "default"}>
                {PATH_LABELS[key] || key}
              </Tag>
              {index < queryPath.length - 1 && <Text type="secondary"> → </Text>}
            </span>
          ))}
        </Space>
        <div style={{ marginTop: 6 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            DNA 双链只负责数据链拓扑，不增加查询层级；先由中通内链按时间圈定位，再经中通周存读取，最后从外链补齐非共有特征。
          </Text>
        </div>
      </Card>
      <Steps direction="vertical" size="small" current={items.length} items={items} />

      {route && ((route.pruned_node_count ?? 0) > 0 || (route.collapsed_messages?.length ?? 0) > 0) && (
        <Card size="small">
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Space size={24}>
              <Statistic
                title="剪枝分叉"
                value={route.pruned_node_count ?? 0}
                prefix={<ScissorOutlined />}
              />
              <Statistic
                title="剪枝记录"
                value={route.pruned_record_count ?? 0}
                prefix={<BranchesOutlined />}
              />
            </Space>
            {(route.collapsed_messages ?? []).map((m, i) => (
              <Alert key={i} type="warning" showIcon message={m} />
            ))}
          </Space>
        </Card>
      )}
    </Space>
  );
}
