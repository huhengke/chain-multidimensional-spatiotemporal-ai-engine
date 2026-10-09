import { SearchOutlined } from "@ant-design/icons";
import {
  AutoComplete,
  Button,
  Card,
  Col,
  Form,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  message,
} from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { errorMessage } from "../api/client";
import {
  levelValues,
  listDatasets,
  queryAggregations,
  queryRecords,
} from "../api/endpoints";
import type { DatasetSummary, NodeBrief, RecordItem } from "../api/types";
import RouteVisualizer from "../components/RouteVisualizer";

interface ConditionForm {
  year?: string;
  month?: string;
}

const TIME_LEVELS = new Set(["year", "season", "month"]);

/** 层级值自动补全：输入联想引擎真实取值（含记录数），选中后仍允许自由输入（引擎侧有包含匹配兜底） */
function LevelAutoComplete(props: {
  dataset: string;
  level: string;
  value?: string;
  onChange?: (v: string) => void;
  width?: number;
  placeholder?: string;
}) {
  const { dataset, level, value, onChange, width = 140, placeholder } = props;
  const [options, setOptions] = useState<{ value: string; label: string }[]>(
    []
  );
  const timer = useRef<number | undefined>(undefined);
  const seq = useRef(0);
  const loaded = useRef(false);

  useEffect(() => {
    setOptions([]);
    loaded.current = false;
  }, [dataset, level]);

  const fetchFor = (term: string) => {
    const id = ++seq.current;
    levelValues(dataset, level, term)
      .then((r) => {
        if (id !== seq.current) return;
        setOptions(
          r.items.map((it) => ({
            value: it.value,
            label: `${it.value}（${it.record_count} 条）`,
          }))
        );
      })
      .catch(() => {
        /* 检索失败不阻断自由输入 */
      });
  };

  return (
    <AutoComplete
      style={{ width }}
      value={value}
      options={options}
      filterOption={false}
      allowClear
      placeholder={placeholder}
      onFocus={() => {
        if (!loaded.current) {
          loaded.current = true;
          fetchFor("");
        }
      }}
      onSearch={(term) => {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(
          () => fetchFor(term.trim()),
          300
        );
      }}
      onChange={(v) => onChange?.(String(v ?? ""))}
    />
  );
}

export default function RecordsPage() {
  const [form] = Form.useForm<ConditionForm>();
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  // 新架构默认使用已注册的产品通用事实数据集。
  const [dataset, setDataset] = useState("oil_monthly_sales");
  const [aggNodes, setAggNodes] = useState<NodeBrief[] | null>(null);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [steps, setSteps] = useState<import("../api/types").RouteStep[]>([]);
  const [loadingAgg, setLoadingAgg] = useState(false);
  const [loadingRec, setLoadingRec] = useState(false);
  const [view, setView] = useState<"agg" | "rec">("rec");
  // 自定义数据集的逐层级筛选值（层级名 → 值），不放入 antd Form（切换数据集时随卸载自动重置）
  const [extras, setExtras] = useState<Record<string, string>>({});

  useEffect(() => {
    void (async () => {
      try {
        const r = await listDatasets();
        // 维表不参与螺旋查询，只列通用事实数据集；同时校正初始选择。
        const items = r.items.filter((d) => d.kind !== "dimension");
        setDatasets(items);
        setDataset((current) =>
          items.some((item) => item.id === current)
            ? current
            : items[0]?.id ?? "oil_monthly_sales"
        );
      } catch (e) {
        message.error(errorMessage(e));
      }
    })();
  }, []);

  const spec = useMemo(
    () => datasets.find((d) => d.id === dataset),
    [datasets, dataset]
  );
  const customLevels = useMemo(
    () => (spec?.levels ?? []).filter((l) => !TIME_LEVELS.has(l)),
    [spec]
  );

  const switchDataset = (id: string) => {
    setDataset(id);
    setExtras({});
    setAggNodes(null);
    setRecords([]);
    setTotal(0);
    setSteps([]);
  };

  const monthValue = (v: ConditionForm) => {
    if (!v.month) return undefined;
    return v.month.length === 6
      ? v.month
      : v.year
        ? `${v.year}${v.month.padStart(2, "0")}`
        : undefined;
  };

  const buildBase = (v: ConditionForm) => {
    const year = v.year;
    const ymm = monthValue(v);
    return { year, ymm };
  };

  const searchAgg = async () => {
    const v = await form.validateFields();
    const { year, ymm } = buildBase(v);
    const cond: Record<string, string> = {};
    if (year) cond.year = year;
    if (ymm) cond.month = ymm;
    for (const lv of customLevels) {
      if (extras[lv]) cond[lv] = extras[lv];
    }
    setLoadingAgg(true);
    setView("agg");
    try {
      const res = await queryAggregations(dataset, cond);
      setAggNodes(res.nodes);
      setSteps(res.route.steps);
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setLoadingAgg(false);
    }
  };

  const searchRec = async (p = 1) => {
    const v = await form.validateFields();
    const { year, ymm } = buildBase(v);
    setLoadingRec(true);
    setView("rec");
    setPage(p);
    try {
      const filters: Record<string, string> = {};
      if (year) filters.year = year;
      if (ymm) filters.month = ymm;
      for (const lv of customLevels) {
        if (extras[lv]) filters[lv] = extras[lv];
      }
      const res = await queryRecords(dataset, filters, p, 20);
      setRecords(res.items);
      setTotal(res.total);
      setSteps(res.route?.steps || []);
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setLoadingRec(false);
    }
  };

  // 聚合列：指标列按数据集声明动态生成（内置/通用一致，LLM/前端不做换算）
  const aggColumns = useMemo(() => {
    const metricCols = (spec?.metrics ?? []).map((m) => ({
      title: m.label || m.name,
      key: m.name,
      render: (_: unknown, r: NodeBrief) => r.aggregations?.[m.name] ?? "—",
    }));
    return [
      { title: "节点", dataIndex: "node_value", key: "value" },
      {
        title: "层级",
        dataIndex: "level_name",
        key: "level",
        render: (v: string) => <Tag>{v}</Tag>,
      },
      { title: "记录数", dataIndex: "record_count", key: "count" },
      ...metricCols,
    ];
  }, [spec]);

  const builtinRecColumns = [
    { title: "年月", dataIndex: "yyyymm", key: "yyyymm" },
    { title: "定位单元", dataIndex: "store_name", key: "store" },
    { title: "产品", dataIndex: "gas_code", key: "gas" },
    { title: "支付方式", dataIndex: "check_mode", key: "mode" },
    { title: "金额(元)", dataIndex: "amount", key: "amount" },
    { title: "吨数", dataIndex: "qty_ton", key: "ton" },
  ];

  // 明细列：通用数据集按声明取「年月 + 各层级取值列 + 指标列」
  const genericRecColumns = useMemo(() => {
    if (!spec) return [];
    const lvk = spec.level_value_key ?? {};
    return [
      { title: "年月", dataIndex: "yyyymm", key: "yyyymm" },
      ...customLevels.map((lv) => ({
        title: spec.level_labels?.[lv] ?? lv,
        dataIndex: lvk[lv] ?? lv,
        key: lv,
      })),
      ...(spec.metrics ?? []).map((m) => ({
        title: m.label || m.name,
        dataIndex: m.column ?? m.source ?? m.name,
        key: m.name,
      })),
    ];
  }, [spec, customLevels]);

  const recColumns = genericRecColumns.length ? genericRecColumns : builtinRecColumns;

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Form form={form} layout="inline">
          <Form.Item label="数据集">
            <Select
              style={{ width: 220 }}
              showSearch
              optionFilterProp="label"
              value={dataset}
              onChange={switchDataset}
              options={datasets.map((d) => ({
                value: d.id,
                label: `${d.label}（${d.id}）`,
              }))}
            />
          </Form.Item>
          <Form.Item name="year" label="年">
            <Input placeholder="2024" style={{ width: 90 }} />
          </Form.Item>
          <Form.Item name="month" label="月">
            <Input placeholder="202403 或 3" style={{ width: 100 }} />
          </Form.Item>
          {customLevels.map((lv) => (
            <Form.Item key={lv} label={spec?.level_labels?.[lv] ?? lv}>
              <LevelAutoComplete
                dataset={dataset}
                level={lv}
                value={extras[lv]}
                onChange={(v) => setExtras({ ...extras, [lv]: v })}
              />
            </Form.Item>
          ))}
          <Form.Item>
            <Space>
              <Button
                type="primary"
                icon={<SearchOutlined />}
                loading={loadingAgg}
                onClick={searchAgg}
              >
                查汇总
              </Button>
              <Button loading={loadingRec} onClick={() => searchRec(1)}>
                查明细
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      <Row gutter={16}>
        <Col span={15}>
          {view === "agg" && aggNodes ? (
            <Card title="聚合结果（直读中环）">
              <Table
                rowKey="node_id"
                columns={aggColumns}
                dataSource={aggNodes}
                pagination={false}
                size="small"
                scroll={{ x: "max-content" }}
              />
            </Card>
          ) : (
            <Card title="明细记录">
              <Statistic title="符合条件记录" value={total} style={{ marginBottom: 12 }} />
              <Table
                rowKey={(r, i) =>
                  String(r.id ?? `${r.yyyymm ?? ""}-${i}`)
                }
                columns={recColumns}
                dataSource={records}
                size="small"
                loading={loadingRec}
                scroll={{ x: "max-content" }}
                pagination={{
                  current: page,
                  total,
                  pageSize: 20,
                  showTotal: (t) => `共 ${t} 条`,
                  onChange: (p) => {
                    void searchRec(p);
                  },
                }}
              />
            </Card>
          )}
        </Col>
        <Col span={9}>
          <Card title="路由路径" style={{ minHeight: 200 }}>
            <RouteVisualizer steps={steps} />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
