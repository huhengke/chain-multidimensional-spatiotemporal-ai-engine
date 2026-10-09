# -*- coding: utf-8 -*-
"""时间规则生成器技能（说明当前热更新能力；修改走基座特征管理界面）。"""
from __future__ import annotations

from agent.python_agent.engine_channel_client import EngineChannelClient
from agent.python_agent.skills.base import BaseSkill, SkillResult


class RuleConfigSkill(BaseSkill):
    name = "rule_config"

    def run(self, question: str, intent: dict,
            channel: EngineChannelClient) -> SkillResult:
        answer = (
            "时间规则生成器支持热加载与版本回滚：修改后无需重启引擎，"
            "月精度数据可派生季节、季度、上下半年和单月/双月；日粒度规则"
            "按当前数据精度生效。实际修改请在“基座特征管理 → 时间规则生成器”由管理员操作。"
        )
        return SkillResult(
            answer=answer,
            thought_chain=[
                {"level": "规则层", "status": "info",
                 "message": "返回热更新能力说明"}
            ],
        )
