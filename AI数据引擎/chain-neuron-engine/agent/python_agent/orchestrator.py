# -*- coding: utf-8 -*-
"""Orchestrator：对话主编排。

意图识别 → 技能路由 → 引擎通道取数（Decimal）→ 组织答案与思维链。
大模型只负责语言组织与意图解析，业务数值全部来自引擎。
"""
from __future__ import annotations

from agent.python_agent.engine_channel_client import EngineChannelClient
from agent.python_agent.llm_client import LLMClient
from agent.python_agent.skills.data_import import DataImportSkill
from agent.python_agent.skills.intent import IntentRecognizer
from agent.python_agent.skills.plan_analysis import PlanAnalysisSkill
from agent.python_agent.skills.rule_config import RuleConfigSkill
from agent.python_agent.skills.sales_query import SalesQuerySkill
from agent.python_agent.skills.semantic_slots import CLARIFY_ANSWER
from engine.core.logging import get_logger

logger = get_logger("agent.orchestrator")

_SKILLS = {
    "sales_query": SalesQuerySkill(),
    "plan_analysis": PlanAnalysisSkill(),
    "data_import": DataImportSkill(),
    "rule_config": RuleConfigSkill(),
}


class Orchestrator:
    def __init__(self):
        self.llm = LLMClient()
        self.recognizer = IntentRecognizer(self.llm)

    def handle(self, question: str,
               channel: EngineChannelClient | None = None) -> dict:
        own_channel = channel is None
        channel = channel or EngineChannelClient()

        try:
            intent = self.recognizer.recognize(question)
            logger.info("意图：%s backend=%s", intent["intent"],
                        intent.get("llm_backend"))

            if intent.get("unresolved"):
                return {
                    "answer": CLARIFY_ANSWER,
                    "intent": intent,
                    "llm_backend": "clarify",
                    "thought_chain": [{
                        "level": "语义解析",
                        "status": "clarify",
                        "message": "未解析出期间、数据集或指标，先向用户澄清",
                    }],
                }

            skill = _SKILLS.get(intent["intent"])
            if skill is None:
                return {
                    "answer": "我可以帮你查询业务数据甲数量、计划完成率、触发数据导入，"
                              "或说明时间派生规则。",
                    "intent": intent,
                    "thought_chain": [],
                }

            result = skill.run(question, intent, channel)
            return {
                "answer": result.answer,
                "intent": intent,
                "thought_chain": result.thought_chain,
                "data": result.data,
                "llm_backend": intent.get("llm_backend") or self.llm.backend,
            }
        finally:
            if own_channel:
                channel.close()
