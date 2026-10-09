# -*- coding: utf-8 -*-
"""数据导入技能：触发导入并报告进度。"""
from __future__ import annotations

from agent.python_agent.engine_channel_client import EngineChannelClient
from agent.python_agent.skills.base import BaseSkill, SkillResult


class DataImportSkill(BaseSkill):
    name = "data_import"

    def run(self, question: str, intent: dict,
            channel: EngineChannelClient) -> SkillResult:
        # “计划”关键词指向新架构计划数据集，默认业务数据甲数据集
        dataset = "oil_sales_plan" if "计划" in question else "oil_monthly_sales"
        started = channel.start_import(dataset)
        task_uuid = started.get("task_uuid")

        task = channel.get_import_task(task_uuid) if task_uuid else {}
        answer = (
            f"已开始{dataset}数据导入，任务号 {task_uuid}。"
            f"当前状态：{task.get('status')}，"
            f"已处理 {task.get('processed', 0)} 条。"
            "可稍后询问“导入进度”。"
        )
        return SkillResult(
            answer=answer,
            data={"task_uuid": task_uuid, "task": task},
            thought_chain=[
                {"level": "数据接入", "status": "started",
                 "message": f"经通道触发 {dataset} 导入"}
            ],
        )
