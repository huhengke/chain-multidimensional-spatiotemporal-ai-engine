import { Card, Col, Progress, Row, Space, Statistic, Table, Tag } from "antd";

import type { ImportTaskInfo } from "../api/types";

export const STATUS_COLOR: Record<string, string> = {
  success: "green",
  partial: "orange",
  failed: "red",
  running: "blue",
  pending: "default",
};

const PHASE_LABEL: Record<string, string> = {
  queued: "排队中",
  preparing: "准备中",
  reading: "读取与映射",
  writing: "批量写入",
  finalizing: "生成特征与聚合",
  materializing: "物化螺旋节点",
  completed: "已完成",
  failed: "失败",
};

/** 导入进度卡片：状态 / 进度条 / 计数 / 错误样例（导入页与注册向导共用）。 */
export default function ImportProgress({
  task,
  percent,
  title,
}: {
  task: ImportTaskInfo;
  percent: number;
  title?: string;
}) {
  return (
    <Card title={title || `导入进度 · ${task.file_name}`}>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Tag color={STATUS_COLOR[task.status]}>{task.status}</Tag>
        <Tag color="geekblue">{PHASE_LABEL[task.phase] || task.phase || "处理中"}</Tag>
        <Progress
          percent={percent}
          format={() =>
            task.total > 0
              ? String(percent) + "%"
              : task.status === "success"
                ? "完成"
                : "处理中"
          }
          status={
            task.status === "failed"
              ? "exception"
              : task.status === "success"
                ? "success"
                : "active"
          }
        />
        <Row gutter={24}>
          <Col>
            <Statistic title="已处理" value={task.processed} />
          </Col>
          {task.total > 0 && (
            <Col>
              <Statistic title="总数" value={task.total} />
            </Col>
          )}
          <Col>
            <Statistic
              title="成功"
              value={task.succeeded}
              valueStyle={{ color: "#3f8600" }}
            />
          </Col>
          <Col>
            <Statistic title="跳过" value={task.skipped} />
          </Col>
          <Col>
            <Statistic
              title="失败"
              value={task.failed}
              valueStyle={{ color: "#cf1322" }}
            />
          </Col>
        </Row>
        {task.message && <Tag>{task.message}</Tag>}
        {task.error_sample.length > 0 && (
          <Table
            size="small"
            rowKey={(r) => `${r.row}-${r.reason}`}
            title={() => "错误样例（前 100 条）"}
            pagination={false}
            dataSource={task.error_sample}
            columns={[
              { title: "行号", dataIndex: "row", width: 100 },
              { title: "原因", dataIndex: "reason" },
            ]}
          />
        )}
      </Space>
    </Card>
  );
}
