import type { AgentChartArtifact } from "../api/types";

export interface LegacyChartExtraction {
  markdown: string;
  visualizations: AgentChartArtifact[];
  warnings: string[];
}

function parseJsonArray(source: string): unknown[] {
  const parsed = JSON.parse(source);
  if (!Array.isArray(parsed)) throw new Error("不是数组");
  return parsed;
}

function seriesNames(title: string, count: number): string[] {
  const movingAverage = title.match(/(\d+\s*个月?均线)/)?.[1]?.replace(/\s+/g, "");
  return Array.from({ length: count }, (_, index) => {
    if (index === 0 && /业务金额/.test(title)) return "月度业务金额";
    if (index === 1 && movingAverage) return movingAverage;
    return `系列 ${index + 1}`;
  });
}

function parseXyChart(source: string, index: number): AgentChartArtifact {
  const lines = source.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.shift() !== "xychart-beta") throw new Error("不是xychart-beta图表");

  let title = "历史折线图";
  let xName = "";
  let yName = "";
  let unit = "";
  let categories: string[] | null = null;
  const dataSeries: Array<Array<number | null>> = [];

  for (const line of lines) {
    let match = line.match(/^title\s+"([^"]+)"$/);
    if (match) {
      title = match[1].trim();
      continue;
    }
    match = line.match(/^x-axis(?:\s+"([^"]*)")?\s+(\[[\s\S]*\])$/);
    if (match) {
      xName = (match[1] || "").trim();
      const values = parseJsonArray(match[2]);
      if (!values.every((value) => typeof value === "string")) throw new Error("横轴包含非文本值");
      categories = values as string[];
      continue;
    }
    match = line.match(/^y-axis\s+"([^"]*)"(?:\s+[-+]?\d+(?:\.\d+)?\s+-->\s+[-+]?\d+(?:\.\d+)?)?$/);
    if (match) {
      yName = match[1].trim();
      unit = yName.match(/[（(]([^）)]+)[）)]/)?.[1] || yName;
      continue;
    }
    match = line.match(/^line\s+(\[[\s\S]*\])$/);
    if (match) {
      const values = parseJsonArray(match[1]);
      if (!values.every((value) => value === null || (typeof value === "number" && Number.isFinite(value)))) {
        throw new Error("数据系列包含非法数值");
      }
      dataSeries.push(values as Array<number | null>);
      continue;
    }
    throw new Error(`存在不支持的指令：${line.slice(0, 40)}`);
  }

  if (!categories || categories.length < 2 || categories.length > 500) {
    throw new Error("横轴分类数量不合法");
  }
  if (!dataSeries.length || dataSeries.length > 8) throw new Error("数据系列数量不合法");
  if (dataSeries.some((values) => values.length !== categories?.length)) {
    throw new Error("数据系列长度与横轴不一致");
  }
  const names = seriesNames(title, dataSeries.length);
  return {
    id: `legacy-chart-${index}`,
    version: 1,
    type: "line",
    title,
    x_axis: { name: xName, categories },
    y_axis: { name: yName, unit },
    series: dataSeries.map((data, seriesIndex) => ({ name: names[seriesIndex], data })),
    source: { datasets: [], metrics: [], period: "历史回答" },
  };
}

export function extractLegacyCharts(content: string): LegacyChartExtraction {
  const visualizations: AgentChartArtifact[] = [];
  const warnings: string[] = [];
  let blockIndex = 0;
  const markdown = content.replace(/```mermaid[ \t]*\r?\n([\s\S]*?)```/gi, (block, source: string) => {
    try {
      visualizations.push(parseXyChart(source, blockIndex));
      blockIndex += 1;
      return "";
    } catch (error) {
      warnings.push(error instanceof Error ? error.message : "历史图表格式无法转换");
      blockIndex += 1;
      return block;
    }
  });
  return { markdown, visualizations, warnings };
}
