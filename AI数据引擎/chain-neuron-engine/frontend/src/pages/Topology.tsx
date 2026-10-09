import { ArrowLeftOutlined } from "@ant-design/icons";
import { Alert, Button, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import { getAgentSession } from "../api/endpoints";
import type { DatasetRouteView } from "../api/types";
import SpatialTopologyDemo from "../components/SpatialTopologyDemo";
import "./Topology.css";

const { Text, Title } = Typography;

export default function TopologyPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const sessionId = params.get("session");
  const [views, setViews] = useState<DatasetRouteView[]>([]);
  const [sessionTitle, setSessionTitle] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(sessionId));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    void getAgentSession(sessionId)
      .then((session) => {
        if (cancelled) return;
        setSessionTitle(session.title);
        const latest = [...(session.messages || [])].reverse().find((message) => message.dataset_views?.length);
        setViews(latest?.dataset_views || []);
      })
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason, "无法读取会话查询路径"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sessionId]);

  const subtitle = useMemo(() => (
    sessionTitle ? `会话：${sessionTitle} · 显示最后一次查询路径` : "未绑定会话 · 显示全部已物化结构"
  ), [sessionTitle]);

  return (
    <div className="topology-page">
      <header className="topology-page-header">
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/agent")}>返回 Agent</Button>
          <div>
            <Title level={4}>AI 数据引擎智慧树</Title>
            <Text type="secondary">{subtitle}</Text>
          </div>
        </Space>
      </header>
      {error && <Alert type="warning" showIcon message={error} closable onClose={() => setError(null)} />}
      {loading ? <div className="topology-page-loading"><Spin /><Text type="secondary">读取会话路径…</Text></div> : <SpatialTopologyDemo datasetViews={views} />}
    </div>
  );
}
