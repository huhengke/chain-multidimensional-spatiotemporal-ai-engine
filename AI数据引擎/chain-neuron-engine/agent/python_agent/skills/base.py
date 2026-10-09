# -*- coding: utf-8 -*-
"""Skill 基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent.python_agent.engine_channel_client import EngineChannelClient


@dataclass
class SkillResult:
    answer: str
    data: dict[str, Any] = field(default_factory=dict)
    thought_chain: list[dict] = field(default_factory=list)


class BaseSkill(ABC):
    name: str = "base"

    @abstractmethod
    def run(self, question: str, intent: dict,
            channel: EngineChannelClient) -> SkillResult:
        """执行技能。所有数值必须来自 channel（引擎 Decimal），禁止自行运算业务值。"""
