import { Alert } from "antd";
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AgentMessage } from "../api/types";
import AgentChart from "./AgentChart";
import { extractLegacyCharts } from "./agentChartParsing";

export default function AgentMessageContent({ message }: { message: AgentMessage }) {
  const legacy = useMemo(() => extractLegacyCharts(message.content), [message.content]);
  const visualizations = message.visualizations?.length
    ? message.visualizations
    : legacy.visualizations;
  const warnings = message.visualizations?.length ? [] : legacy.warnings;

  return (
    <div className="agent-message-content">
      <div className="agent-markdown">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            table: ({ children }) => (
              <div className="agent-markdown-table-wrap"><table>{children}</table></div>
            ),
            a: ({ children, href }) => (
              <a href={href} target="_blank" rel="noreferrer">{children}</a>
            ),
          }}
        >
          {legacy.markdown}
        </ReactMarkdown>
      </div>
      {visualizations.map((visualization) => (
        <AgentChart key={visualization.id} visualization={visualization} />
      ))}
      {warnings.map((warning, index) => (
        <Alert
          key={`${warning}-${index}`}
          className="agent-chart-warning"
          type="warning"
          showIcon
          message="历史图表格式无法转换"
          description={warning}
        />
      ))}
    </div>
  );
}
