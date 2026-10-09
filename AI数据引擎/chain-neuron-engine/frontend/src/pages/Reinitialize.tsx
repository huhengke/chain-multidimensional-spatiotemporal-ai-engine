import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Descriptions,
  Input,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";

import { errorMessage } from "../api/client";
import {
  analyzeReinitialize,
  executeReinitialize,
  listDisabledDatasets,
  previewReinitialize,
  readinessReinitialize,
  restoreDataset,
} from "../api/endpoints";
import type {
  DatasetSummary,
  ReinitializePreview,
  ReinitializeReadiness,
  ReinitializeResult,
} from "../api/types";

const { Title, Paragraph, Text } = Typography;

export default function ReinitializePage() {
  const [preview, setPreview] = useState<ReinitializePreview | null>(null);
  const [result, setResult] = useState<ReinitializeResult | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [allowMissingSources, setAllowMissingSources] = useState(false);
  const [loading, setLoading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [readiness, setReadiness] = useState<ReinitializeReadiness | null>(null);
  const [disabledDatasets, setDisabledDatasets] = useState<DatasetSummary[]>([]);

  const loadPreview = async () => {
    setLoading(true);
    try {
      setPreview(await previewReinitialize());
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setLoading(false);
    }
  };

  const loadDisabledDatasets = async () => {
    try {
      const response = await listDisabledDatasets();
      setDisabledDatasets(response.items);
    } catch (error) {
      message.error(errorMessage(error));
    }
  };

  useEffect(() => {
    void loadPreview();
    void checkReadiness();
    void loadDisabledDatasets();
  }, []);

  const checkReadiness = async () => {
    try {
      setReadiness(await readinessReinitialize());
    } catch (error) {
      message.error(errorMessage(error));
    }
  };

  const analyze = async () => {
    setAnalyzing(true);
    try {
      setPreview(await analyzeReinitialize());
      await checkReadiness();
      message.success("数据画像和特征建议已生成，未修改业务数据");
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setAnalyzing(false);
    }
  };

  const execute = async () => {
    setExecuting(true);
    try {
      const next = await executeReinitialize(confirmation, allowMissingSources);
      setResult(next);
      setConfirmation("");
      setAllowMissingSources(false);
      await loadPreview();
      await checkReadiness();
      await loadDisabledDatasets();
      message.success("清库完成，已进入人工逐数据集重建");
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setExecuting(false);
    }
  };

  const restoreBase = async (dataset: string) => {
    try {
      const response = await restoreDataset(dataset);
      message.success(response.message || `数据集 ${dataset} 已恢复`);
      await loadDisabledDatasets();
      await loadPreview();
      await checkReadiness();
    } catch (error) {
      message.error(errorMessage(error));
    }
  };

  const columns = [
    {
      title: "数据集",
      dataIndex: "label",
      render: (label: string, row: ReinitializePreview["datasets"][number]) => (
        <Space direction="vertical" size={0}>
          <Text strong>{label}</Text>
          <Text type="secondary">{row.dataset}</Text>
        </Space>
      ),
    },
    {
      title: "类型",
      dataIndex: "kind",
      render: (kind: string) => <Tag>{kind}</Tag>,
    },
    { title: "根目录文件", dataIndex: "source_file" },
    { title: "现有行数", dataIndex: "current_rows" },
    {
      title: "AI/规则画像",
      dataIndex: "profile",
      render: (profile: Record<string, unknown> | undefined) =>
        profile ? (
          <Text type="success">已分析：{String(profile.row_count ?? "-")} 行</Text>
        ) : (
          <Text type="secondary">尚未分析</Text>
        ),
    },
    {
      title: "状态",
      dataIndex: "source_exists",
      render: (exists: boolean) =>
        exists ? <Tag color="green">可导入</Tag> : <Tag color="red">缺文件</Tag>,
    },
  ];

  const proposalItems = (preview?.proposals || []).map((proposal) => ({
    key: String(proposal.proposal_id || Math.random()),
    label: `${String(proposal.dataset || "")} · ${String(proposal.feature_type || "特征")}`,
    children: (
      <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
        {JSON.stringify(proposal, null, 2)}
      </pre>
    ),
  }));
  const onlyMissingSourceBlockers = Boolean(
    readiness && readiness.blockers.length > 0 && readiness.blockers.every(
      (blocker) => blocker.startsWith("数据集 ") && blocker.includes("来源不可用"),
    ),
  );
  const canExecute = Boolean(readiness?.ready || (allowMissingSources && onlyMissingSourceBlockers));

  return (
    <Space direction="vertical" size={18} style={{ display: "flex" }}>
      <div>
        <Title level={3} style={{ marginBottom: 4 }}>
          一次性架构升级验收
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          这是验证导入架构升级的管理员工具：字段分析、建议确认、备份清库、全量重导和验收只执行一次，不是日常初始化功能。
        </Paragraph>
      </div>

      <Alert
        type="warning"
        showIcon
        message="执行一次性验收会清空事实表、维表、螺旋节点和可重建特征"
        description={`本次流程会先保存声明快照，再注销界面注册数据集、停用基座数据集并清理业务数据。清理后不会自动导入旧声明，必须在本页恢复基座或重新注册界面数据集，完成 AI 字段分析和人工确认特征，再执行导入。当前状态：${preview?.reinitialize_state?.status || "未启动"}`}
      />

      <Space>
        <Button onClick={() => void loadPreview()} loading={loading}>
          读取重初始化预览
        </Button>
        <Button type="primary" onClick={() => void analyze()} loading={analyzing}>
          分析字段并生成建议（不清库）
        </Button>
        <Button onClick={() => void checkReadiness()} loading={loading}>
          检查注销前置条件
        </Button>
      </Space>

      {readiness && (
        <Alert
          type={readiness.ready ? "success" : "error"}
          showIcon
          message={readiness.ready ? "条件已满足，可以开始注销全部 → 重新导入" : "暂不能开始注销全部"}
          description={
            <Space direction="vertical" size={4}>
              <Text>
                推荐库：{readiness.catalog.ok ? `v${readiness.catalog.version}，${readiness.catalog.active_entries} 条有效经验` : "不可用"}
                {"；"}模型通道：{readiness.model.ready ? "可用" : "未通过运行态检查"}
                {"；"}数据源：{readiness.datasets.filter((item) => item.ok).length}/{readiness.dataset_count} 可用
              </Text>
              {!readiness.ready && (
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {readiness.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
                </ul>
              )}
              {onlyMissingSourceBlockers && (
                <Text type="warning">旧声明中有来源文件已不在根目录；如果本次只做“注销全部、清空运行态、重新手工注册”，可勾选下方的人工重建选项。</Text>
              )}
              {(readiness.stale_import_tasks?.length || 0) > 0 && (
                <Text type="warning">
                  发现历史遗留导入任务记录 {readiness.stale_import_tasks?.length} 条；当前进程未在执行，不作为本次前置阻断项。
                </Text>
              )}
            </Space>
          }
        />
      )}

      {preview && (
        <>
          <Descriptions size="small" bordered column={2}>
            <Descriptions.Item label="模型配置">
              {String((preview.model as { active?: string }).active || "deepseek_harness")}
            </Descriptions.Item>
            <Descriptions.Item label="数据集数">{preview.datasets.length}</Descriptions.Item>
            <Descriptions.Item label="分析状态">
              {preview.analysis_mode || "仅预览"}
            </Descriptions.Item>
            <Descriptions.Item label="建议数">{preview.proposals?.length || 0}</Descriptions.Item>
            <Descriptions.Item label="清库后动作" span={2}>
              {preview.reinitialize_state?.next_step || "先分析和核对字段建议，再执行一次性清库"}
            </Descriptions.Item>
          </Descriptions>

          {preview.warnings.length > 0 && (
            <Alert
              type="error"
              showIcon
              message="存在阻断项"
              description={
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                </ul>
              }
            />
          )}

          <Table
            rowKey="dataset"
            size="small"
            loading={loading || analyzing}
            columns={columns}
            dataSource={preview.datasets}
            pagination={false}
            scroll={{ x: "max-content" }}
          />

          {disabledDatasets.length > 0 && (
            <Card
              size="small"
              title="待恢复的基座声明"
              extra={<Text type="secondary">已注销基座不显示在数据集主列表</Text>}
            >
              <Table
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={disabledDatasets}
                columns={[
                  { title: "数据集", dataIndex: "label" },
                  { title: "标识", dataIndex: "id" },
                  { title: "来源文件", dataIndex: "source_file" },
                  {
                    title: "操作",
                    render: (_: unknown, row: DatasetSummary) => (
                      <Button size="small" onClick={() => void restoreBase(row.id)}>
                        恢复基座声明
                      </Button>
                    ),
                  },
                ]}
              />
            </Card>
          )}

          {proposalItems.length > 0 && (
            <Collapse items={proposalItems} />
          )}

          <div>
            <Text strong>执行确认</Text>
            <Paragraph type="secondary" style={{ margin: "4px 0 8px" }}>
              只有管理员在完成分析并核对文件后，才能执行清空和重导。
            </Paragraph>
            {onlyMissingSourceBlockers && (
              <Checkbox checked={allowMissingSources} onChange={(event) => setAllowMissingSources(event.target.checked)} style={{ marginBottom: 8 }}>
                允许缺失旧来源，仅清空并进入人工重新注册（不会自动导入）
              </Checkbox>
            )}
            <Space.Compact style={{ width: "100%" }}>
              <Input
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                placeholder="输入 CLEAR_AND_REIMPORT"
              />
              <Button
                danger
                type="primary"
                disabled={confirmation !== "CLEAR_AND_REIMPORT" || !canExecute || ["started", "completed"].includes(preview.reinitialize_state?.status || "")}
                loading={executing}
                onClick={() => void execute()}
              >
                执行一次性清库重导
              </Button>
            </Space.Compact>
          </div>

          {preview.reinitialize_state?.status === "awaiting_manual_import" && (
            <Alert
              type="info"
              showIcon
              message="当前处于人工重建阶段"
              description="请先在本页恢复基座数据集；界面注册数据集需要重新注册。注册向导会使用经验推荐库 + AI + 规则生成字段建议，确认后再按主数据、事实数据顺序导入。"
            />
          )}

          {preview.reinitialize_state?.status === "awaiting_manual_registration" && (
            <Alert
              type="info"
              showIcon
              message="当前处于注销全部后的人工重建阶段"
              description="请先在上方恢复仍需保留的基座声明；界面注册数据集需要重新注册。注册向导会使用经验推荐库 + AI + 规则生成字段建议，确认后再执行导入。已停用的旧声明不会自动恢复。"
            />
          )}
        </>
      )}

      {result && (
        <Alert
          type="success"
          showIcon
          message={result.message}
          description={
            <Space direction="vertical">
              <Text>备份：{result.backup_path}</Text>
              {result.tasks.length > 0 ? result.tasks.map((task) => (
                <Text key={task.task_uuid}>
                  {task.dataset}：{task.task_uuid}（{task.file_name}）
                </Text>
              )) : (
                <Text>未启动自动导入任务，请按数据集管理中的 AI 建议逐个手工导入。</Text>
              )}
            </Space>
          }
        />
      )}
    </Space>
  );
}
