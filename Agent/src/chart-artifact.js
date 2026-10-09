const MAX_CATEGORIES = 500;
const MAX_SERIES = 8;

function invalid(message) {
  const error = new Error(message);
  error.code = "INVALID_CHART_ARTIFACT";
  error.payload = { code: error.code, message };
  return error;
}

function cleanText(value, label, { required = true, maxLength = 120 } = {}) {
  const text = String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (required && !text) throw invalid(`${label}不能为空`);
  if (text.length > maxLength) throw invalid(`${label}不能超过${maxLength}个字符`);
  return text;
}

function cleanTextList(value, label, { min = 1, max = 32, itemLength = 80 } = {}) {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    throw invalid(`${label}数量必须在${min}至${max}之间`);
  }
  return value.map((item, index) =>
    cleanText(item, `${label}[${index}]`, { maxLength: itemLength }),
  );
}

export function sanitizeChartArtifact(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw invalid("图表参数必须是对象");
  }
  if (Number(input.version) !== 1) throw invalid("图表协议版本必须为1");
  if (input.type !== "line") throw invalid("当前只支持line折线图");

  const categories = cleanTextList(input.x_axis?.categories, "横轴分类", {
    min: 2,
    max: MAX_CATEGORIES,
    itemLength: 80,
  });
  if (!Array.isArray(input.series) || input.series.length < 1 || input.series.length > MAX_SERIES) {
    throw invalid(`数据系列数量必须在1至${MAX_SERIES}之间`);
  }

  const series = input.series.map((item, seriesIndex) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw invalid(`数据系列[${seriesIndex}]必须是对象`);
    }
    if (!Array.isArray(item.data) || item.data.length !== categories.length) {
      throw invalid(`数据系列[${seriesIndex}]长度必须与横轴分类一致`);
    }
    const data = item.data.map((value, valueIndex) => {
      if (value === null) return null;
      if (typeof value !== "number" || !Number.isFinite(value)) {
        throw invalid(`数据系列[${seriesIndex}].data[${valueIndex}]必须是有限数值或null`);
      }
      return value;
    });
    return {
      name: cleanText(item.name, `数据系列[${seriesIndex}]名称`, { maxLength: 80 }),
      data,
    };
  });

  const source = input.source;
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    throw invalid("图表来源不能为空");
  }

  return {
    version: 1,
    type: "line",
    title: cleanText(input.title, "图表标题", { maxLength: 120 }),
    x_axis: {
      name: cleanText(input.x_axis?.name, "横轴名称", { required: false, maxLength: 40 }),
      categories,
    },
    y_axis: {
      name: cleanText(input.y_axis?.name, "纵轴名称", { required: false, maxLength: 40 }),
      unit: cleanText(input.y_axis?.unit, "纵轴单位", { required: false, maxLength: 24 }),
    },
    series,
    source: {
      datasets: cleanTextList(source.datasets, "来源数据集", { min: 1, max: 16, itemLength: 80 }),
      metrics: cleanTextList(source.metrics, "来源指标", { min: 1, max: 32, itemLength: 80 }),
      period: cleanText(source.period, "来源期间", { required: false, maxLength: 80 }),
    },
  };
}
