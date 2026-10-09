# -*- coding: utf-8 -*-
"""通用数据集查询技能：只消费注册表与统一拓扑查询接口。"""
from __future__ import annotations

from decimal import Decimal

from engine.query.experience import actual_sales_scope
from agent.python_agent.engine_channel_client import EngineChannelClient
from agent.python_agent.skills.base import BaseSkill, SkillResult

_METRIC_LABELS = {
    "total_amount": "业务金额",
    "sale_amount": "业务金额",
    "monthlyamount": "业务金额",
    "amount": "业务金额",
    "real_amount": "实收金额",
    "pay_amount": "支付金额",
    "total_qty_ton": "业务吨数",
    "monthlysales": "业务吨数",
    "qty_ton": "业务吨数",
    "total_qty_liter": "业务升数",
    "monthlyqty": "业务升数",
    "qty_liter": "业务升数",
    "monsalesplan": "吨数业务计划",
}


class SalesQuerySkill(BaseSkill):
    name = "sales_query"

    def run(
        self,
        question: str,
        intent: dict,
        channel: EngineChannelClient,
    ) -> SkillResult:
        datasets = _resolve_datasets(intent, question, channel)
        dataset = datasets[0]
        conditions = dict(intent.get("conditions") or {})
        # 答案前缀用提问原词；resolve_conditions 编译出的编码只用于过滤，不给人看
        display_conditions = dict(conditions)
        # 先用第一个目标数据集的真实节点值校准显式层级条件；地点语义
        # 仍由引擎基座按每个数据集的 org_name/org_code 特征分别解析。
        conditions = channel.resolve_conditions(dataset, conditions)
        filters = dict(intent.get("filters") or {})
        filters.update(conditions)
        group_by = intent.get("group_by")
        # 基座语义条件必须由引擎确认真实存在才下传：无命中的条件会被基座
        # 阻断整条查询，而提问用词被误当地点时（如"各月业务数量如何"）本来就不该过滤。
        base_filters, notes = channel.confirm_base_terms(
            datasets, dict(intent.get("base_filters") or {})
        )
        payload = {
            "datasets": datasets,
            "dataset": dataset,
            "conditions": conditions,
            "filters": filters,
            "base_filters": base_filters,
            "group_by": [group_by] if group_by else [],
            "metrics": _requested_metrics(dataset, question, intent),
            "comparisons": list(intent.get("comparisons") or []),
            "detail": bool(intent.get("detail")),
        }
        data = channel.unified_query(payload)
        if len(datasets) > 1 or payload["comparisons"]:
            # 派生指标（日均/欠进度）只在单期间路径摊算：分组对比里逐组调用
            # 会把一次问题变成 N 次除法，主答案仍给合计，缺口如实说明。
            if intent.get("derived_metrics"):
                notes = list(notes) + [
                    "分组对比本次给合计，日均/欠进度请按单个期间提问（如「2026年8月日均业务数量」）"
                ]
            return self._format_batch(
                question, display_conditions, payload, data, notes
            )
        actual = data.get("actual") or {}
        chain = _route_chain(actual.get("route"))
        return self._format(
            dataset, display_conditions, data, chain, notes,
            payload.get("base_filters") or {},
        )

    def _format_batch(
        self,
        question: str,
        conditions: dict,
        payload: dict,
        data: dict,
        notes: list[str] | None = None,
    ) -> SkillResult:
        actual_root = data.get("actual") or {}
        datasets = actual_root.get("datasets") or {}
        query_plan = data.get("query_plan") or {}
        # 批量期间标签由引擎在顶层透传（子查询里只剩编译后的日期）
        batch_period_label = (query_plan.get("resolved_period") or {}).get("label")
        scope = _scope_text(
            conditions,
            payload.get("base_filters") or {},
            batch_period_label or _resolved_year_label(query_plan),
        )
        lines: list[str] = []
        chain: list[dict] = []
        period_labels = query_plan.get("periods") or {}
        # 查询范围只在第一条结果前露出一次：每个数据集都重复一遍
        # "示例定位单元丙 2024 …"会把答案变成回声，也没增加信息。
        scope_shown = False

        def take_scope() -> str:
            nonlocal scope_shown
            if scope_shown or not scope:
                return ""
            scope_shown = True
            return scope

        for dataset, item in datasets.items():
            label = str(item.get("label") or dataset)
            current = item.get("current") or {}
            current_plan = ((query_plan.get("queries") or {}).get(dataset) or {}).get("current") or {}
            metric_keys = _display_metric_keys(
                current_plan, current, question
            )
            # 即使当前期间没有命中，也保留该数据集的真实路由。
            # 前端需要明确展示“已参与查询、当前无数据”，不能因为没有结果
            # 就让该数据集从思维链和拓扑视图中消失。
            chain.extend(
                _route_chain(current.get("route"), prefix=label)
            )
            if not current.get("available"):
                if current.get("unsupported_conditions"):
                    lines.append(
                        current.get("unsupported_message")
                        or f"{take_scope()}{label}：本次条件无法映射到该数据集的层级，未执行查询。"
                    )
                    continue
                if current.get("precision_gap"):
                    lines.append(
                        f"{take_scope()}{label}：无法按当前请求的日粒度精确计算。"
                        f"{current.get('precision_gap_message') or '请检查数据集时间字段精度。'}"
                    )
                    continue
                lines.append(f"{take_scope()}{label}：当前期间没有命中数据。")
                continue
            nodes = current.get("nodes") or []
            if current_plan.get("group_by") and nodes:
                # 分组提问必须逐项给数：批量路径此前只报年度合计，
                # 于是"各月业务数量如何"在两个数据集上都退化成同一个总数。
                lines.append(f"{take_scope()}{label}（共 {len(nodes)} 项）：")
                for node in nodes:
                    node_text = _format_selected_metrics(
                        node.get("aggregations") or {}, current_plan, metric_keys
                    )
                    lines.append(
                        f"· {_node_label(node)}：{node_text}"
                        f"（记录 {node.get('record_count', 0)} 条）"
                    )
            else:
                current_text = _format_selected_metrics(
                    current.get("aggregations") or {}, current_plan, metric_keys
                )
                current_period = (
                    batch_period_label
                    or (current_plan.get("resolved_period") or {}).get("label")
                    or period_labels.get("current")
                    or "当前期间"
                )
                current_parts = [f"当前期 {current_period}：{current_text}"]
                if current.get("order_count") is not None:
                    current_parts.append(f"记录数 {current['order_count']} 笔")
                current_parts.append(f"明细记录 {current.get('record_count', 0)} 条")
                lines.append(f"{take_scope()}{label}：" + "；".join(current_parts))
            for comparison in payload.get("comparisons") or []:
                comparison_item = (item.get("comparisons") or {}).get(comparison)
                if not comparison_item:
                    continue
                comparison_metrics = comparison_item.get("metrics") or {}
                comparison_label = "同比" if comparison == "yoy" else "环比"
                comparison_text = _format_comparison_metrics(
                    comparison_metrics, current_plan, metric_keys, comparison_label
                )
                if comparison_text:
                    lines.append(
                        f"  {comparison_label}（{comparison_item.get('period') or '比较期间'}）："
                        f"{comparison_text}"
                    )
        totals = actual_root.get("totals") or {}
        if totals.get("sales_amount") is not None:
            total_parts = [f"业务金额 {totals['sales_amount']} 元"]
            if totals.get("order_count") is not None:
                total_parts.append(f"记录数 {totals['order_count']} 笔")
            total_parts.append(f"明细记录 {totals.get('record_count', 0)} 条")
            lines.append("通用事实合计：" + "；".join(total_parts) + "。")
        missing = totals.get("datasets_without_data") or []
        if missing:
            lines.append("说明：" + "、".join(str(item) for item in missing) + " 在当前期间无命中，已保留在本次查询范围内，未把无数据误当成未参与查询。")
        precision_gap = totals.get("datasets_precision_gap") or []
        if precision_gap:
            lines.append("说明：" + "、".join(str(item) for item in precision_gap) + " 已识别到时间粒度不足，未按更粗的时间粒度凑数。")
        unsupported = totals.get("datasets_unsupported") or []
        if unsupported:
            lines.append("说明：" + "、".join(str(item) for item in unsupported) + " 的条件无法在该数据集确认（层级或基座语义），未参与合计。")
        if not lines:
            lines.append("没有找到符合条件的通用事实数据。")
        warnings = list(notes or []) + list(data.get("warnings") or [])
        if warnings:
            rendered = "\n".join(lines)
            remaining = [
                str(warning) for warning in warnings
                if str(warning) and str(warning) not in rendered
            ]
            if remaining:
                lines.append("提示：" + "；".join(dict.fromkeys(remaining)))
        return SkillResult(
            answer="\n".join(lines),
            data=data,
            thought_chain=chain,
        )

    def _format(
        self,
        dataset: str,
        conditions: dict,
        data: dict,
        chain: list[dict],
        notes: list[str] | None = None,
        base_filters: dict | None = None,
    ) -> SkillResult:
        actual = data.get("actual") or {}
        query_plan = data.get("query_plan") or {}
        label = str(query_plan.get("label") or dataset)
        nodes = actual.get("nodes") or []
        if actual.get("unsupported_conditions"):
            # 条件被静默丢弃会让"示例区域甲组织层级一"答成全公司总额（X18）：如实报错，不凑数
            return SkillResult(
                answer=(
                    actual.get("unsupported_message")
                    or f"{label}：本次条件无法映射到该数据集的层级，未执行查询。"
                ) + _notes_suffix(notes),
                data=data,
                thought_chain=chain,
            )
        if actual.get("precision_gap"):
            # 粒度缺口不是"没有数据"：必须说明是时间精度不够，并给出缺口口径
            return SkillResult(
                answer=(
                    f"{label}：无法按当前请求的日粒度精确计算。"
                    f"{actual.get('precision_gap_message') or '请检查数据集时间字段精度。'}"
                    + _notes_suffix(notes)
                ),
                data=data,
                thought_chain=chain,
            )
        if actual.get("items") is not None:
            return SkillResult(
                answer=f"{label}命中 {actual.get('record_count', 0)} 条记录。"
                + _notes_suffix(notes),
                data=data,
                thought_chain=chain,
            )
        # 事实表聚合兜底时拓扑节点为空，但条件命中的记录数才是有无结果的依据
        if not nodes and not actual.get("record_count"):
            return SkillResult(
                answer=f"没有找到符合条件的「{label}」数据。" + _notes_suffix(notes),
                data=data,
                thought_chain=chain,
            )

        scope = _scope_text(
            conditions,
            base_filters,
            (query_plan.get("resolved_period") or {}).get("label")
            or _resolved_year_label(query_plan),
        )
        if query_plan.get("group_by"):
            lines = [f"{scope}{label}（共 {len(nodes)} 项）："]
            for node in nodes:
                metrics = _format_aggregations(node.get("aggregations") or {}, query_plan)
                lines.append(
                    f"· {_node_label(node)}：{metrics}（记录 "
                    f"{node.get('record_count', 0)} 条）"
                )
            return SkillResult(
                answer="\n".join(lines) + _notes_suffix(notes),
                data=data,
                thought_chain=chain,
            )

        totals = actual.get("aggregations") or {}
        parts = [_format_aggregations(totals, query_plan)]
        if actual.get("order_count") is not None:
            parts.append(f"记录数 {actual['order_count']} 笔")
        parts.append(f"明细记录 {actual.get('record_count', 0)} 条")
        answer = f"{scope}{label}：" + "；".join(part for part in parts if part) + "。"
        if data.get("warnings"):
            answer += "\n提示：" + "；".join(data["warnings"])
        return SkillResult(
            answer=answer + _notes_suffix(notes), data=data, thought_chain=chain
        )


def _node_label(node: dict) -> str:
    """分组节点显示：引擎按事实表函数依赖补出的名称优先，编码保留便于对账。"""
    value = str(node.get("node_value") or "")
    display = str(node.get("display_value") or "").strip()
    return f"{display}（{value}）" if display and display != value else value


def _notes_suffix(notes: list[str] | None) -> str:
    items = [str(note) for note in (notes or []) if str(note).strip()]
    return "\n提示：" + "；".join(dict.fromkeys(items)) if items else ""


def _requested_metrics(dataset: str, question: str, intent: dict) -> list[str]:
    requested = [
        str(value) for value in (intent.get("metrics") or []) if str(value).strip()
    ]
    if requested:
        return requested
    if any(word in question for word in ("金额", "业务金额", "业务金额")):
        if dataset == "oil_monthly_sales":
            return ["monthlyamount"]
        if dataset == "non_oil_sales":
            return ["total_amount"]
    if "吨" in question:
        return ["monsalesplan"] if dataset == "oil_sales_plan" else ["monthlysales"]
    if "升" in question:
        return ["monthlyqty"]
    return []


def _resolve_datasets(
    intent: dict,
    question: str,
    channel: EngineChannelClient,
) -> list[str]:
    explicit = [
        str(value).strip() for value in (intent.get("datasets") or [])
        if str(value).strip()
    ]
    if explicit:
        return list(dict.fromkeys(explicit))
    if str(intent.get("dataset_scope") or "single") != "all_actual":
        return [str(intent.get("dataset") or "oil_monthly_sales").strip()]

    policy = actual_sales_scope()
    include_kinds = {
        str(value).strip() for value in (policy.get("include_kinds") or ["generic"])
    }
    exclude_terms = [
        str(value).strip().casefold()
        for value in (policy.get("exclude_terms") or ["计划", "指标", "预算", "plan"])
    ]
    try:
        items = channel.list_datasets().get("items", [])
    except Exception:
        return [str(intent.get("dataset") or "oil_monthly_sales").strip()]
    result: list[str] = []
    for item in items:
        if str(item.get("kind") or "") not in include_kinds or item.get("disabled"):
            continue
        texts = [str(item.get("label") or ""), *(str(v) for v in item.get("aliases") or [])]
        searchable = " ".join(texts).casefold()
        if any(term and term in searchable for term in exclude_terms):
            continue
        dataset = str(item.get("id") or "").strip()
        if dataset:
            result.append(dataset)
    return result or [str(intent.get("dataset") or "oil_monthly_sales").strip()]


def _format_aggregations(aggregations: dict, query_plan: dict | None = None) -> str:
    labels = (query_plan or {}).get("metric_labels") or {}
    return "；".join(
        f"{_metric_label(key, labels)} {value}"
        + _metric_unit(key, str(_metric_label(key, labels)))
        for key, value in aggregations.items()
    ) or "无指标值"


def _display_metric_keys(query_plan: dict, actual: dict, question: str) -> list[str]:
    aggregations = actual.get("aggregations") or {}
    if not aggregations:
        return []
    labels = query_plan.get("metric_labels") or {}
    entries = list(aggregations)

    def label(key: str) -> str:
        return _metric_label(key, labels)

    def score(key: str) -> int:
        text = f"{key} {label(key)}".casefold()
        value = 0
        if any(token in text for token in ("amount", "revenue", "金额", "业务金额")):
            value += 100
        if any(token in text for token in ("sale", "业务")):
            value += 20
        if any(token in text for token in ("real", "pay", "支付", "实收")):
            value -= 20
        if key in {"monthlyamount", "total_amount", "sale_amount"}:
            value += 10
        return value

    if any(word in question for word in ("金额", "业务金额", "业务金额")):
        return [max(entries, key=score)]
    primary = max(entries, key=score)
    selected = [primary]
    # 未限定单位的“业务”保留同一事实数据集的数量指标，便于产品结果
    # 同时展示金额、吨数和升数；其他通用事实只显示主业务指标。
    for key in entries:
        text = f"{key} {label(key)}".casefold()
        if key == primary:
            continue
        if any(token in text for token in ("吨", "升", "liter", "ton", "qty")):
            selected.append(key)
    return selected[:4]


def _metric_unit(key: str, label: str) -> str:
    text = f"{key} {label}".casefold()
    if any(token in text for token in ("amount", "revenue", "金额", "业务金额", "实收", "支付")):
        return " 元"
    if any(token in text for token in ("ton", "吨")) or key in {"monthlysales", "monsalesplan"}:
        return " 吨"
    if any(token in text for token in ("liter", "升")) or key == "monthlyqty":
        return " 升"
    return ""


def _format_selected_metrics(
    aggregations: dict,
    query_plan: dict,
    keys: list[str],
) -> str:
    labels = query_plan.get("metric_labels") or {}
    parts = []
    for key in keys:
        if key not in aggregations:
            continue
        name = _metric_label(key, labels)
        parts.append(f"{name} {aggregations[key]}{_metric_unit(key, str(name))}")
    return "；".join(parts) or _format_aggregations(aggregations)


def _format_comparison_metrics(
    metrics: dict,
    query_plan: dict,
    keys: list[str],
    label: str,
) -> str:
    labels = query_plan.get("metric_labels") or {}
    parts = []
    for key in keys:
        item = metrics.get(key)
        if not item:
            continue
        name = _metric_label(key, labels)
        rate = item.get("rate")
        rate_text = "基期为0，无法计算变化率" if rate is None else f"{label}率 {rate}%"
        parts.append(
            f"{name} {item.get('previous')}{_metric_unit(key, str(name))}，"
            f"差额 {item.get('delta')}{_metric_unit(key, str(name))}，{rate_text}"
        )
    return "；".join(parts)


def _metric_label(key: str, labels: dict) -> str:
    declared = str(labels.get(key) or "").strip()
    # 数据集没声明中文名（或声明得和物理列名一样）时显示物理列名：
    # 与 Harness 同一套回退，缺 label 不该把 "None" 甩给业务方看。
    # 要出中文名请在「数据集管理 → 编辑声明」里补齐指标 label。
    if not declared or declared == key:
        return _METRIC_LABELS.get(key) or key
    return declared


def _resolved_year_label(plan: dict) -> str:
    """相对年份（今年/去年/明年）由时间基座编译成 year 层级条件。

    年份口径标签只在引擎审计里，答案前缀不露出来的话，业务方无从确认
    "去年"到底取的是哪一年。批量路径的标签在顶层，单数据集路径在
    time_foundation 审计里。
    """
    plan = plan or {}
    year_period = (
        plan.get("resolved_year")
        or (plan.get("time_foundation") or {}).get("resolved_year")
        or {}
    )
    return str(year_period.get("label") or "")


def _scope_text(
    conditions: dict,
    base_filters: dict | None = None,
    period_label: str | None = None,
) -> str:
    parts: list[str] = []
    for key in ("year", "season", "month", "big_area", "branch", "station"):
        value = conditions.get(key)
        if value:
            for item in (value if isinstance(value, list) else [value]):
                text = str(item)
                # "去年8月"这类相对年份只下传裸月份，年份由引擎补齐。
                # 前缀按提问原词显示，不能把内部编码 "8" 甩给业务方。
                if key == "month" and text.isdigit() and len(text) <= 2:
                    text = f"{int(text)}月"
                parts.append(text)
    location = (base_filters or {}).get("organization") or (base_filters or {}).get("location")
    if location:
        parts.insert(0, str(location))
    # 引擎换算出来的期间口径（上周/本月/4月第三周）要显式露出，
    # 否则"第三周"这类语义只能靠业务方猜我们按哪种周口径算的。
    if period_label:
        parts.insert(0, str(period_label))
    unique = list(dict.fromkeys(part for part in parts if part.strip()))
    return (" ".join(unique) + " ") if unique else ""


def _route_chain(route: dict | None, prefix: str = "") -> list[dict]:
    if not route:
        return []
    return [
        {
            "level": (
                f"{prefix} / {step.get('level_label', step.get('level_name'))}"
                if prefix else step.get("level_label", step.get("level_name"))
            ),
            "value": step.get("value"),
            "status": step.get("status"),
            "message": step.get("message"),
        }
        for step in route.get("steps", [])
    ]
