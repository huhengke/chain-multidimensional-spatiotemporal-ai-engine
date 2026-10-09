import { DownloadOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import { LineChart } from "echarts/charts";
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsType } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef, useState } from "react";

import type { AgentChartArtifact } from "../api/types";

echarts.use([
  LineChart,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const CHART_COLORS = ["#1677ff", "#0f9f6e", "#c77d00", "#d1495b", "#5965b8", "#00838f"];

function safeFileName(value: string): string {
  const cleaned = value.replace(/[\\/:*?"<>|]/g, "-").replace(/\s+/g, " ").trim();
  return (cleaned || "Agent图表").slice(0, 80);
}

function displayNumber(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
}

export default function AgentChart({ visualization }: { visualization: AgentChartArtifact }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const [renderError, setRenderError] = useState("");
  const [downloadError, setDownloadError] = useState("");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    setRenderError("");
    let chart: EChartsType | null = null;
    try {
      chart = echarts.init(container, undefined, { renderer: "canvas" });
      chartRef.current = chart;
      const unit = visualization.y_axis.unit || "";
      chart.setOption({
        aria: {
          enabled: true,
          decal: { show: true },
          description: `${visualization.title}，包含${visualization.series.length}个数据系列。`,
        },
        animationDuration: 350,
        color: CHART_COLORS,
        grid: { left: 24, right: 24, top: 54, bottom: 34, containLabel: true },
        legend: { top: 8, type: "scroll" },
        tooltip: {
          trigger: "axis",
          valueFormatter: (value: unknown) => `${displayNumber(value)}${unit ? ` ${unit}` : ""}`,
        },
        dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none" }],
        xAxis: {
          type: "category",
          name: visualization.x_axis.name || "",
          boundaryGap: false,
          data: visualization.x_axis.categories,
          axisLabel: { hideOverlap: true },
        },
        yAxis: {
          type: "value",
          name: [visualization.y_axis.name, unit && `(${unit})`].filter(Boolean).join(" "),
          axisLabel: { formatter: (value: number) => displayNumber(value) },
        },
        series: visualization.series.map((series, index) => ({
          name: series.name,
          type: "line",
          data: series.data,
          connectNulls: false,
          showSymbol: visualization.x_axis.categories.length <= 36,
          symbol: "circle",
          symbolSize: 7,
          lineStyle: { width: index === 0 ? 3 : 2, type: index === 1 ? "dashed" : "solid" },
          itemStyle: { borderWidth: 1, borderColor: "#ffffff" },
          emphasis: { focus: "series" },
        })),
      });
    } catch (error) {
      setRenderError(error instanceof Error ? error.message : "图表加载失败");
    }

    const resizeObserver = new ResizeObserver(() => chart?.resize());
    resizeObserver.observe(container);
    return () => {
      resizeObserver.disconnect();
      chartRef.current = null;
      chart?.dispose();
    };
  }, [visualization]);

  const download = () => {
    setDownloadError("");
    try {
      const chart = chartRef.current;
      if (!chart) throw new Error("图表尚未加载完成");
      const anchor = document.createElement("a");
      anchor.download = `${safeFileName(visualization.title)}-${Date.now()}.png`;
      anchor.href = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#ffffff" });
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "PNG下载失败");
    }
  };

  return (
    <section className="agent-chart" aria-label={visualization.title}>
      <header className="agent-chart-header">
        <div>
          <strong>{visualization.title}</strong>
          <small>
            {visualization.source.datasets.length ? visualization.source.datasets.join("、") : "历史回答"}
            {visualization.source.period ? ` · ${visualization.source.period}` : ""}
          </small>
        </div>
        <Tooltip title="将当前图表下载为 PNG 图片">
          <Button
            size="small"
            icon={<DownloadOutlined />}
            onClick={download}
            disabled={Boolean(renderError)}
          >
            下载 PNG
          </Button>
        </Tooltip>
      </header>
      <div ref={containerRef} className="agent-chart-canvas" />
      {renderError && <div className="agent-chart-error">图表加载失败：{renderError}</div>}
      {downloadError && <div className="agent-chart-error">下载失败：{downloadError}</div>}
    </section>
  );
}
