import { DeleteOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";

import { errorMessage } from "../api/client";
import { analyzeRules, fetchRules, reloadRules, updateRules } from "../api/endpoints";
import type { RuleSuggestion } from "../api/types";

const { Text } = Typography;

interface MappingRow {
  input: string;
  output: string;
}

interface ConditionRow {
  condition: string;
  output: string;
}

interface EditableRule {
  name: string;
  source: string;
  precision: string;
  mappingRows: MappingRow[];
  conditionRows: ConditionRow[];
}

interface RuleSuggestionDraft extends EditableRule {
  id: string;
  rationale: string;
  sourceType: string;
}

interface RulesData {
  version: number;
  loaded_at: string | null;
  rules: Array<Record<string, unknown>>;
  yaml: string;
  read_only?: boolean;
  source?: string;
  asset_id?: string;
  profile_id?: string;
  catalog_id?: string;
}

const SOURCE_OPTIONS = [
  { value: "month", label: "月份" },
  { value: "weekday", label: "星期" },
  { value: "date", label: "日期" },
  { value: "iso_week", label: "ISO 周（引擎按日期计算，无需映射）" },
  { value: "day", label: "日历日（引擎按日期取值，无需映射）" },
];

/** 引擎直接计算的内置来源：不需要映射表或条件 */
const BUILTIN_SOURCES = new Set(["iso_week", "day"]);

const PRECISION_OPTIONS = [
  { value: "", label: "不限制精度" },
  { value: "month", label: "需要月份精度" },
  { value: "day", label: "需要日精度" },
];

const RULE_LABELS: Record<string, string> = {
  season: "季节",
  quarter: "季度",
  half_year: "上半年/下半年",
  month_parity: "单月/双月",
  week: "周（ISO 周）",
  day: "日",
  weekday_type: "工作日/周末",
  holiday: "节日或活动日",
  solar_term: "二十四节气",
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const textValue = (value: unknown) => (value == null ? "" : String(value));

function normalizeRule(raw: Record<string, unknown>): EditableRule {
  const mappingRows = isRecord(raw.mapping)
    ? Object.entries(raw.mapping).map(([input, output]) => ({
        input,
        output: textValue(output),
      }))
    : [];
  const conditionRows = Array.isArray(raw.rules)
    ? raw.rules.filter(isRecord).map((item) => ({
        condition: textValue(item.condition),
        output: textValue(item.value),
      }))
    : [];
  return {
    name: textValue(raw.name),
    source: textValue(raw.source),
    precision: textValue(raw.precision),
    mappingRows,
    conditionRows,
  };
}

function blankRule(): EditableRule {
  return {
    name: "",
    source: "month",
    precision: "",
    mappingRows: [{ input: "", output: "" }],
    conditionRows: [],
  };
}

function normalizeSuggestion(raw: RuleSuggestion, index: number): RuleSuggestionDraft {
  return {
    id: raw.id || `suggestion-${index}`,
    name: textValue(raw.name),
    source: textValue(raw.source),
    precision: textValue(raw.precision),
    mappingRows: isRecord(raw.mapping)
      ? Object.entries(raw.mapping).map(([input, output]) => ({ input, output: textValue(output) }))
      : [],
    conditionRows: Array.isArray(raw.rules)
      ? raw.rules.map((item) => ({ condition: textValue(item.condition), output: textValue(item.value) }))
      : [],
    rationale: textValue(raw.rationale),
    sourceType: textValue(raw.source_type || "ai"),
  };
}

function withoutSuggestionMeta(suggestion: RuleSuggestionDraft): EditableRule {
  return {
    name: suggestion.name,
    source: suggestion.source,
    precision: suggestion.precision,
    mappingRows: suggestion.mappingRows,
    conditionRows: suggestion.conditionRows,
  };
}

function quoteYaml(value: string): string {
  return JSON.stringify(value ?? "");
}

function serializeRules(version: number, rules: EditableRule[]): string {
  const lines = [`# 时间派生规则（表单生成；保存后自动热更新）`, `version: ${version}`, "time_derivations:"];
  rules.forEach((rule) => {
    lines.push(`  - name: ${quoteYaml(rule.name)}`);
    lines.push(`    source: ${quoteYaml(rule.source)}`);
    if (rule.precision) lines.push(`    precision: ${quoteYaml(rule.precision)}`);
    if (rule.mappingRows.length) {
      lines.push("    mapping:");
      rule.mappingRows.forEach((row) => {
        lines.push(`      ${quoteYaml(row.input)}: ${quoteYaml(row.output)}`);
      });
    }
    if (rule.conditionRows.length) {
      lines.push("    rules:");
      rule.conditionRows.forEach((row) => {
        lines.push(`      - condition: ${quoteYaml(row.condition)}`);
        lines.push(`        value: ${quoteYaml(row.output)}`);
      });
    }
  });
  return `${lines.join("\n")}\n`;
}

function validateRules(rules: EditableRule[]): string | null {
  const names = new Set<string>();
  for (const rule of rules) {
    if (!rule.name.trim()) return "存在未填写名称的规则";
    if (names.has(rule.name.trim())) return `规则“${rule.name}”重复`;
    names.add(rule.name.trim());
    if (!rule.source.trim()) return `规则“${rule.name}”没有填写来源字段`;
    if (!rule.mappingRows.length && !rule.conditionRows.length && !BUILTIN_SOURCES.has(rule.source)) {
      return `规则“${rule.name}”至少需要一条映射或条件`;
    }
    if (rule.mappingRows.some((row) => !row.input.trim() || !row.output.trim())) {
      return `规则“${rule.name}”存在空的映射项`;
    }
    if (rule.conditionRows.some((row) => !row.condition.trim() || !row.output.trim())) {
      return `规则“${rule.name}”存在空的条件项`;
    }
  }
  return null;
}

const sourceLabel = (source: string) =>
  SOURCE_OPTIONS.find((item) => item.value === source)?.label || source || "未设置";

export default function RulesPage({ embedded = false }: { embedded?: boolean }) {
  const [data, setData] = useState<RulesData | null>(null);
  const [rules, setRules] = useState<EditableRule[]>([]);
  const [idea, setIdea] = useState("");
  const [suggestions, setSuggestions] = useState<RuleSuggestionDraft[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [yaml, setYaml] = useState("");
  const [saving, setSaving] = useState(false);
  const readOnly = Boolean(data?.read_only);

  const load = async () => {
    try {
      const d = (await fetchRules()) as RulesData;
      setData(d);
      setRules((d.rules || []).map(normalizeRule));
      setYaml(d.yaml);
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const updateRule = (index: number, patch: Partial<EditableRule>) => {
    setRules((current) =>
      current.map((rule, itemIndex) => (itemIndex === index ? { ...rule, ...patch } : rule)),
    );
  };

  const updateMapping = (ruleIndex: number, rowIndex: number, patch: Partial<MappingRow>) => {
    setRules((current) =>
      current.map((rule, itemIndex) => {
        if (itemIndex !== ruleIndex) return rule;
        return {
          ...rule,
          mappingRows: rule.mappingRows.map((row, index) =>
            index === rowIndex ? { ...row, ...patch } : row,
          ),
        };
      }),
    );
  };

  const updateCondition = (ruleIndex: number, rowIndex: number, patch: Partial<ConditionRow>) => {
    setRules((current) =>
      current.map((rule, itemIndex) => {
        if (itemIndex !== ruleIndex) return rule;
        return {
          ...rule,
          conditionRows: rule.conditionRows.map((row, index) =>
            index === rowIndex ? { ...row, ...patch } : row,
          ),
        };
      }),
    );
  };

  const updateSuggestion = (id: string, patch: Partial<EditableRule>) => {
    setSuggestions((current) =>
      current.map((suggestion) => (suggestion.id === id ? { ...suggestion, ...patch } : suggestion)),
    );
  };

  const updateSuggestionMapping = (id: string, rowIndex: number, patch: Partial<MappingRow>) => {
    setSuggestions((current) =>
      current.map((suggestion) => {
        if (suggestion.id !== id) return suggestion;
        return {
          ...suggestion,
          mappingRows: suggestion.mappingRows.map((row, index) =>
            index === rowIndex ? { ...row, ...patch } : row,
          ),
        };
      }),
    );
  };

  const updateSuggestionCondition = (id: string, rowIndex: number, patch: Partial<ConditionRow>) => {
    setSuggestions((current) =>
      current.map((suggestion) => {
        if (suggestion.id !== id) return suggestion;
        return {
          ...suggestion,
          conditionRows: suggestion.conditionRows.map((row, index) =>
            index === rowIndex ? { ...row, ...patch } : row,
          ),
        };
      }),
    );
  };

  const analyze = async () => {
    if (readOnly) {
      message.warning("当前时间规则已固定在活动 CNTS Catalog 中；请修改候选 Catalog");
      return;
    }
    if (!idea.trim()) {
      message.warning("请先输入想要配置的规则，例如：把上周、双11和季度加入时间语义");
      return;
    }
    setAnalyzing(true);
    try {
      const result = await analyzeRules({ idea, rules: data?.rules || [] });
      const nextSuggestions = (result.suggestions || []).map(normalizeSuggestion);
      setSuggestions(nextSuggestions);
      if (nextSuggestions.length) {
        message.success(`${nextSuggestions.length} 条建议已生成，请修改并逐条加入规则表单`);
      } else {
        message.info("暂未识别到明确的值映射或条件规则，请补充更具体的月份、星期或日期条件");
      }
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const acceptSuggestion = (suggestion: RuleSuggestionDraft) => {
    const validation = validateRules([withoutSuggestionMeta(suggestion)]);
    if (validation) {
      message.warning(`这条建议还不能加入：${validation}`);
      return;
    }
    setRules((current) => [...current, withoutSuggestionMeta(suggestion)]);
    setSuggestions((current) => current.filter((item) => item.id !== suggestion.id));
    message.success("建议已加入规则表单，尚未生效；请检查后保存");
  };

  const saveForm = async () => {
    if (readOnly) {
      message.warning("活动 CNTS Catalog 为只读，不能原地热更新时间规则");
      return;
    }
    const validation = validateRules(rules);
    if (validation) {
      message.warning(validation);
      return;
    }
    const nextYaml = serializeRules(Math.max(1, Number(data?.version || 1) + 1), rules);
    setSaving(true);
    try {
      const result = await updateRules(nextYaml);
      message.success(result.message || "规则已热更新");
      await load();
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const saveAdvanced = async () => {
    if (readOnly) {
      message.warning("活动 CNTS Catalog 为只读，不能原地保存 YAML");
      return;
    }
    if (!yaml.trim()) {
      message.warning("规则 YAML 不能为空");
      return;
    }
    setSaving(true);
    try {
      const result = await updateRules(yaml);
      message.success(result.message || "规则已热更新");
      await load();
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const reload = async () => {
    if (readOnly) {
      await load();
      message.info("已重新读取活动 Catalog 中的固定时间规则");
      return;
    }
    try {
      await reloadRules();
      message.success("已重新加载");
      await load();
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: "100%", minWidth: 0 }}>
      <Alert
        type="info"
        showIcon
        message={embedded ? "时间规则生成器" : "时间规则生成器已经改成表单维护"}
        description="这是基座特征管理中的时间语义配置工具：用来把季度、季节、周、单月/双月、节气和自定义活动日生成标准标签。原始日期/年月字段仍进入中通主干，规则结果由基座编译成主干可识别的标准条件；普通维护不需要编写 YAML。"
      />

      {readOnly && (
        <Alert
          type="warning"
          showIcon
          message="当前展示活动 CNTS Catalog 的固定时间规则"
          description={`Catalog：${data?.catalog_id || "-"}；只允许查看。修改规则需生成新的候选 Catalog，验收后再切换。`}
        />
      )}

      <Alert
        type="warning"
        showIcon
        message="时间规则不会直接修改事实数据"
        description="AI/经验分析只生成可编辑的待确认建议；加入规则表单并保存后才会热更新。日粒度规则必须有日粒度时间字段，二十四节气预置规则是可编辑日期窗口，若业务要求天文级精确值请在此处维护或接入日历服务。"
      />

      <Card title="AI/经验融合规则建议">
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Input.TextArea
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            placeholder="例如：把 3、4、5 月设置为春季，并把 11 月 11 日标记为双11"
            autoSize={{ minRows: 2, maxRows: 5 }}
          />
          <Space wrap>
            <Button type="primary" loading={analyzing} disabled={readOnly} onClick={() => void analyze()}>
              分析并生成待确认建议
            </Button>
            <Text type="secondary">
              AI/经验只生成草稿；你可以先修改，再点击“加入规则表单”，最后保存才会生效。
            </Text>
          </Space>

          {suggestions.map((suggestion) => (
            <Card
              key={suggestion.id}
              size="small"
              title={
                <Space>
                  <Tag color="blue">待确认</Tag>
                  <span>{suggestion.name || "未命名建议"}</span>
                  <Tag>{suggestion.sourceType || "ai"}</Tag>
                </Space>
              }
              extra={
                <Space>
                  <Button type="primary" disabled={readOnly} onClick={() => acceptSuggestion(suggestion)}>
                    加入规则表单
                  </Button>
                  <Button
                    danger
                    type="link"
                    disabled={readOnly}
                    onClick={() => setSuggestions((current) => current.filter((item) => item.id !== suggestion.id))}
                  >
                    忽略
                  </Button>
                </Space>
              }
            >
              <Space direction="vertical" style={{ width: "100%" }} size={10}>
                <Text type="secondary">建议依据：{suggestion.rationale || "请结合业务口径确认"}</Text>
                <Space wrap>
                  <Input
                    addonBefore="规则名称"
                    value={suggestion.name}
                    style={{ width: 260 }}
                    onChange={(e) => updateSuggestion(suggestion.id, { name: e.target.value })}
                  />
                  <Input
                    addonBefore="来源字段"
                    value={suggestion.source}
                    style={{ width: 260 }}
                    onChange={(e) => updateSuggestion(suggestion.id, { source: e.target.value })}
                  />
                  <Select
                    value={suggestion.source || undefined}
                    options={SOURCE_OPTIONS}
                    placeholder="选择来源含义"
                    style={{ width: 150 }}
                    onChange={(value) => updateSuggestion(suggestion.id, { source: value })}
                  />
                  <Select
                    value={suggestion.precision}
                    options={PRECISION_OPTIONS}
                    style={{ width: 170 }}
                    onChange={(value) => updateSuggestion(suggestion.id, { precision: value })}
                  />
                </Space>

                <Card
                  size="small"
                  title="输入值 → 输出结果"
                  extra={
                    <Button
                      size="small"
                      icon={<PlusOutlined />}
                      onClick={() =>
                        updateSuggestion(suggestion.id, {
                          mappingRows: [...suggestion.mappingRows, { input: "", output: "" }],
                        })
                      }
                    >
                      添加映射
                    </Button>
                  }
                >
                  {suggestion.mappingRows.length ? suggestion.mappingRows.map((row, rowIndex) => (
                    <Space key={`${suggestion.id}-mapping-${rowIndex}`} style={{ display: "flex", marginBottom: 8 }}>
                      <Input
                        addonBefore="输入"
                        value={row.input}
                        placeholder="例如 3,4,5"
                        style={{ width: 300 }}
                        onChange={(e) => updateSuggestionMapping(suggestion.id, rowIndex, { input: e.target.value })}
                      />
                      <Input
                        addonBefore="输出"
                        value={row.output}
                        placeholder="例如 春季"
                        style={{ width: 260 }}
                        onChange={(e) => updateSuggestionMapping(suggestion.id, rowIndex, { output: e.target.value })}
                      />
                      <Button
                        danger
                        type="text"
                        icon={<DeleteOutlined />}
                        aria-label="删除建议映射"
                        onClick={() =>
                          updateSuggestion(suggestion.id, {
                            mappingRows: suggestion.mappingRows.filter((_, index) => index !== rowIndex),
                          })
                        }
                      />
                    </Space>
                  )) : <Text type="secondary">暂无映射项。</Text>}
                </Card>

                <Card
                  size="small"
                  title="条件 → 输出结果"
                  extra={
                    <Button
                      size="small"
                      icon={<PlusOutlined />}
                      onClick={() =>
                        updateSuggestion(suggestion.id, {
                          conditionRows: [...suggestion.conditionRows, { condition: "", output: "" }],
                        })
                      }
                    >
                      添加条件
                    </Button>
                  }
                >
                  {suggestion.conditionRows.length ? suggestion.conditionRows.map((row, rowIndex) => (
                    <Space key={`${suggestion.id}-condition-${rowIndex}`} style={{ display: "flex", marginBottom: 8 }}>
                      <Input
                        addonBefore="条件"
                        value={row.condition}
                        placeholder="例如 month=11 and day=11"
                        style={{ width: 440 }}
                        onChange={(e) => updateSuggestionCondition(suggestion.id, rowIndex, { condition: e.target.value })}
                      />
                      <Input
                        addonBefore="输出"
                        value={row.output}
                        placeholder="例如 双11"
                        style={{ width: 260 }}
                        onChange={(e) => updateSuggestionCondition(suggestion.id, rowIndex, { output: e.target.value })}
                      />
                      <Button
                        danger
                        type="text"
                        icon={<DeleteOutlined />}
                        aria-label="删除建议条件"
                        onClick={() =>
                          updateSuggestion(suggestion.id, {
                            conditionRows: suggestion.conditionRows.filter((_, index) => index !== rowIndex),
                          })
                        }
                      />
                    </Space>
                  )) : <Text type="secondary">暂无条件项。</Text>}
                </Card>
              </Space>
            </Card>
          ))}
        </Space>
      </Card>

      <Card
        title="时间派生规则"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={reload}>
              重新加载
            </Button>
            <Button icon={<PlusOutlined />} disabled={readOnly} onClick={() => setRules((current) => [...current, blankRule()])}>
              新增规则
            </Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={readOnly} onClick={() => void saveForm()}>
              保存表单并热更新
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Text type="secondary">
            当前版本 v{data?.version ?? "-"} · 最后加载时间 {data?.loaded_at || "-"} · 共 {rules.length} 条规则
          </Text>
          {rules.length === 0 ? (
            <Empty description="暂无规则，可点击“新增规则”" />
          ) : (
            rules.map((rule, ruleIndex) => (
              <Card
                key={`${ruleIndex}-${rule.name}`}
                size="small"
                title={`规则 ${ruleIndex + 1}：${RULE_LABELS[rule.name] || rule.name || "未命名"}`}
                extra={
                  <Button
                    danger
                    type="link"
                    icon={<DeleteOutlined />}
                    disabled={readOnly}
                    onClick={() => setRules((current) => current.filter((_, index) => index !== ruleIndex))}
                  >
                    删除规则
                  </Button>
                }
              >
                <Space direction="vertical" style={{ width: "100%" }} size={10}>
                  <Space wrap>
                    <Input
                      addonBefore="规则名称"
                      value={rule.name}
                      placeholder="例如 season"
                      style={{ width: 260 }}
                      onChange={(e) => updateRule(ruleIndex, { name: e.target.value })}
                    />
                    <Input
                      addonBefore="来源字段"
                      value={rule.source}
                      placeholder="例如 month"
                      style={{ width: 260 }}
                      onChange={(e) => updateRule(ruleIndex, { source: e.target.value })}
                    />
                    <Select
                      value={rule.source || undefined}
                      options={SOURCE_OPTIONS}
                      placeholder="选择来源含义"
                      style={{ width: 150 }}
                      onChange={(value) => updateRule(ruleIndex, { source: value })}
                    />
                    <Select
                      value={rule.precision}
                      options={PRECISION_OPTIONS}
                      style={{ width: 170 }}
                      onChange={(value) => updateRule(ruleIndex, { precision: value })}
                    />
                  </Space>
                  <Text type="secondary">
                    当前来源含义：{sourceLabel(rule.source)}；精度：{rule.precision || "不限制"}
                  </Text>

                  <Card size="small" title="输入值 → 输出结果" extra={<Button size="small" icon={<PlusOutlined />} disabled={readOnly} onClick={() => updateRule(ruleIndex, { mappingRows: [...rule.mappingRows, { input: "", output: "" }] })}>添加映射</Button>}>
                    {rule.mappingRows.length ? rule.mappingRows.map((row, rowIndex) => (
                      <Space key={`${ruleIndex}-mapping-${rowIndex}`} style={{ display: "flex", marginBottom: 8 }}>
                        <Input
                          addonBefore="输入"
                          value={row.input}
                          placeholder="例如 3,4,5"
                          style={{ width: 300 }}
                          onChange={(e) => updateMapping(ruleIndex, rowIndex, { input: e.target.value })}
                        />
                        <Input
                          addonBefore="输出"
                          value={row.output}
                          placeholder="例如 春季"
                          style={{ width: 260 }}
                          onChange={(e) => updateMapping(ruleIndex, rowIndex, { output: e.target.value })}
                        />
                        <Button
                          danger
                          type="text"
                          icon={<DeleteOutlined />}
                          disabled={readOnly}
                          aria-label="删除映射"
                          onClick={() => updateRule(ruleIndex, { mappingRows: rule.mappingRows.filter((_, index) => index !== rowIndex) })}
                        />
                      </Space>
                    )) : <Text type="secondary">暂无映射项；如果使用条件规则，可以在下面添加条件。</Text>}
                  </Card>

                  <Card size="small" title="条件 → 输出结果" extra={<Button size="small" icon={<PlusOutlined />} disabled={readOnly} onClick={() => updateRule(ruleIndex, { conditionRows: [...rule.conditionRows, { condition: "", output: "" }] })}>添加条件</Button>}>
                    {rule.conditionRows.length ? rule.conditionRows.map((row, rowIndex) => (
                      <Space key={`${ruleIndex}-condition-${rowIndex}`} style={{ display: "flex", marginBottom: 8 }}>
                        <Input
                          addonBefore="条件"
                          value={row.condition}
                          placeholder="例如 month=11 and day=11"
                          style={{ width: 440 }}
                          onChange={(e) => updateCondition(ruleIndex, rowIndex, { condition: e.target.value })}
                        />
                        <Input
                          addonBefore="输出"
                          value={row.output}
                          placeholder="例如 双11"
                          style={{ width: 260 }}
                          onChange={(e) => updateCondition(ruleIndex, rowIndex, { output: e.target.value })}
                        />
                        <Button
                          danger
                          type="text"
                          icon={<DeleteOutlined />}
                          disabled={readOnly}
                          aria-label="删除条件"
                          onClick={() => updateRule(ruleIndex, { conditionRows: rule.conditionRows.filter((_, index) => index !== rowIndex) })}
                        />
                      </Space>
                    )) : <Text type="secondary">暂无条件项；如果使用值映射，可以只维护上面的映射。</Text>}
                  </Card>
                </Space>
              </Card>
            ))
          )}
        </Space>
      </Card>

      <Card
        title="高级 YAML（可选）"
        extra={
          <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={readOnly} onClick={() => void saveAdvanced()}>
            校验并保存 YAML
          </Button>
        }
      >
        <Collapse
          items={[
            {
              key: "advanced-yaml",
              label: "展开 YAML 编辑器（熟悉配置格式时使用）",
              children: (
                <Input.TextArea
                  value={yaml}
                  disabled={readOnly}
                  onChange={(e) => setYaml(e.target.value)}
                  autoSize={{ minRows: 18, maxRows: 30 }}
                  style={{ fontFamily: "monospace", fontSize: 12 }}
                />
              ),
            },
          ]}
        />
        <Text type="secondary" style={{ display: "block", marginTop: 8 }}>
          高级 YAML 保存后仍会执行服务端校验、版本快照和热加载；普通用户不需要打开这里。
        </Text>
      </Card>
    </Space>
  );
}
