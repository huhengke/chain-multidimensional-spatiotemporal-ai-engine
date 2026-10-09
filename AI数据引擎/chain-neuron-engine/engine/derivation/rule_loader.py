# -*- coding: utf-8 -*-
"""规则加载器：解析/校验时间派生规则，版本化快照存档。"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from engine.core.config import ENGINE_CONFIG_DIR, load_yaml_config
from engine.core.errors import BadRequestError
from engine.core.logging import get_logger
from engine.core.config import DATA_DIR

logger = get_logger("derivation.rules")

RULES_FILE = ENGINE_CONFIG_DIR / "time_derivation_rules.yaml"
SNAPSHOT_DIR = DATA_DIR / "rule_snapshots"


class RuleLoader:
    def __init__(self, path: Path = RULES_FILE):
        self.path = path
        self.version: int = 0
        self.raw: dict = {}
        self.rules: list[dict] = []
        self.loaded_at: datetime | None = None
        self.load()

    def load(self) -> dict:
        self.raw = load_yaml_config(self.path)
        self._validate(self.raw)
        self.version = int(self.raw.get("version", 1))
        self.rules = self.raw.get("time_derivations", [])
        self.loaded_at = datetime.now()
        logger.info("派生规则加载 version=%d rules=%d", self.version, len(self.rules))
        return self.raw

    def reload(self) -> dict:
        return self.load()

    def snapshot(self) -> Path:
        """版本快照存档（供回滚）。"""
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target = SNAPSHOT_DIR / f"time_derivation_rules_v{self.version}.yaml"
        shutil.copy2(self.path, target)
        return target

    def get_rule(self, name: str) -> dict | None:
        for r in self.rules:
            if r.get("name") == name:
                return r
        return None

    @staticmethod
    def _validate(raw: dict) -> None:
        rules = raw.get("time_derivations")
        if not isinstance(rules, list) or not rules:
            raise BadRequestError("time_derivation_rules.yaml 缺少 time_derivations 列表")
        names = set()
        for r in rules:
            name = r.get("name")
            if not name:
                raise BadRequestError("存在缺少 name 的派生规则")
            if name in names:
                raise BadRequestError(f"派生规则重名：{name}")
            names.add(name)
            # iso_week / day 等内置来源由引擎直接计算，不需要 mapping/rules
            if r.get("source") in {"iso_week", "day"}:
                continue
            if "mapping" not in r and "rules" not in r:
                raise BadRequestError(f"规则 {name} 缺少 mapping 或 rules")
