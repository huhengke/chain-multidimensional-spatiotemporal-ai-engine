# -*- coding: utf-8 -*-
"""TimeDerivationEngine：按规则从时间值派生属性。

支持精度：
- month（YYYYMM）：月份映射类规则生效；day 级规则返回 None（降级）；
- day（YYYYMMDD 或 datetime）：月份映射、星期、日期条件和日历日规则生效。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from engine.core.logging import get_logger
from engine.derivation.rule_loader import RuleLoader

logger = get_logger("derivation.engine")


class TimeDerivationEngine:
    def __init__(self, loader: RuleLoader | None = None):
        self.loader = loader or RuleLoader()

    def reload(self) -> None:
        self.loader.reload()

    @property
    def version(self) -> int:
        return self.loader.version

    def derive_month(self, year: int | str, month: int | str) -> dict[str, Any]:
        """月精度派生：执行 month 来源规则；day 级规则明确降级为 None。"""
        m = int(month)
        result: dict[str, Any] = {}
        for rule in self.loader.rules:
            name = rule["name"]
            if rule.get("precision") == "day":
                result[name] = None  # 精度不足，降级
                continue
            if rule.get("source") == "month":
                result[name] = self._match_mapping(rule.get("mapping", {}), m)
        return result

    def derive_date(self, d: date) -> dict[str, Any]:
        """日精度派生：按规则来源计算标准时间标签。"""
        result: dict[str, Any] = {}
        for rule in self.loader.rules:
            name = rule["name"]
            src = rule.get("source")
            if src == "month":
                result[name] = self._match_mapping(rule.get("mapping", {}), d.month)
            elif src == "iso_week":
                iso = d.isocalendar()
                result[name] = f"{iso[0]}W{iso[1]:02d}"
            elif src == "weekday":
                result[name] = self._match_mapping(rule.get("mapping", {}), d.weekday())
            elif src == "date":
                result[name] = self._match_date_rules(rule.get("rules", []), d)
            elif src == "day":
                # 日层级取值：ISO 日历日（与事实表 __time 同口径，周层级同理带年份）
                result[name] = d.isoformat()
        return result

    @staticmethod
    def _match_mapping(mapping: dict, key: int) -> str | None:
        for keys, value in mapping.items():
            if str(key) in [k.strip() for k in str(keys).split(",")]:
                return value
        return None

    @staticmethod
    def _match_date_rules(rules: list, d: date) -> str | None:
        """极简条件求值：仅支持 month/day 的 =,>=,<=,and 组合。"""
        for rule in rules:
            cond = rule.get("condition", "")
            if TimeDerivationEngine._eval_condition(cond, d):
                return rule.get("value")
        return None

    @staticmethod
    def _eval_condition(cond: str, d: date) -> bool:
        try:
            parts = [p.strip() for p in cond.replace(" and ", " and ").split("and")]
            env = {"month": d.month, "day": d.day}
            ok = True
            for p in parts:
                for op in (">=", "<=", "==", "="):
                    if op in p:
                        k, v = p.split(op)
                        k, v = k.strip(), int(v.strip())
                        if op == "=" or op == "==":
                            ok &= env[k] == v
                        elif op == ">=":
                            ok &= env[k] >= v
                        elif op == "<=":
                            ok &= env[k] <= v
                        break
                else:
                    return False
            return ok
        except Exception:
            logger.exception("日期条件求值失败：%s", cond)
            return False
