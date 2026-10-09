"""层级取值的运行时解析：把自然语言名称编译成该层级真实可过滤的值。

设计约束（见《问题排查经验手册.md》A8 / X18）：

- 不建静态别名映射表。名称列属于哪个层级，由事实表自身的函数依赖推出来。
- 解析不出来就如实回传，绝不猜值、绝不静默丢弃条件——静默丢弃会让
  "示例区域甲组织层级一"答成全公司总额，比报错危险得多。
"""
from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import String, func, or_, select
from sqlalchemy.orm import Session

from engine.storage.dataset_registry import DatasetSpec, registry
from engine.storage.dynamic_table import get_dynamic_table

# 函数依赖要扫表分组，代价不低；数据只在导入时变化，给一个短 TTL 足够。
_CACHE_TTL_SECONDS = 300.0
_PAIR_CACHE: dict[tuple[str, int], tuple[float, dict[str, list[str]]]] = {}
_VALUE_CACHE: dict[tuple[str, int, str, str], tuple[float, dict[str, Any]]] = {}

TIME_LEVELS = {
    "time", "date", "day", "week", "weekofyear", "month", "yyyymm", "yyyy",
    "year", "season", "quarter", "period", "datetime", "timestamp",
}
_CODE_SUFFIXES = ("code", "codes", "id", "ids", "no", "key")
_NAME_SUFFIXES = ("name", "names", "label", "title")

# 调用方（两套 Agent）的语义槽名 → 中文维度词。这里只有"槽位叫什么"，
# 没有任何"某个数据集有哪层"的知识：具体层级由下面按 level_labels 运行时匹配。
SLOT_WORDS: dict[str, tuple[str, ...]] = {
    "big_area": ("组织层级一", "组织层级一编码"),
    "branch": ("组织层级二", "组织层级二"),
    "area": ("组织层级三",),
    "station": ("定位单元", "服务节点", "店"),
    "store_name": ("定位单元", "服务节点", "店"),
    "gas_code": ("产品", "商品"),
    "category": ("大类", "类型", "品类"),
    "grade": ("类别代码",),
    "check_mode": ("支付方式", "支付"),
}


def normalize_key(text: Any) -> str:
    """层级键/列名的宽松归一：`check_mode` 与 `checkmode` 视为同一个东西。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(text).casefold())


def _stem(column: str, suffixes: tuple[str, ...]) -> str:
    folded = normalize_key(column)
    for suffix in suffixes:
        if folded.endswith(suffix) and len(folded) > len(suffix):
            return folded[: -len(suffix)]
    return folded


def _registry_revision() -> int:
    return int(getattr(registry, "revision", 0) or 0)


def _filterable_levels(spec: DatasetSpec) -> list[str]:
    return [level for level in spec.levels if normalize_key(level) not in TIME_LEVELS]


def _candidate_columns(spec: DatasetSpec, table) -> list[str]:
    metric_columns = {metric.column for metric in spec.metrics}
    level_columns = {spec.level_value_key.get(level, level) for level in spec.levels}
    return [
        column.name for column in table.columns
        if isinstance(column.type, String)
        and column.name not in metric_columns
        and column.name not in level_columns
        and column.name not in {"__time", "id"}
    ]


def _determined_levels(session: Session, table, candidate: str, level_columns: list[str]) -> list[str]:
    """该名称列能唯一决定哪些层级列（candidate → level 函数依赖）。"""
    if not level_columns:
        return []
    target = table.c[candidate]
    grouped = (
        select(
            *[func.count(func.distinct(table.c[column])).label(f"d{index}")
              for index, column in enumerate(level_columns)]
        )
        .select_from(table)
        .where(target.isnot(None), target != "")
        .group_by(target)
        .subquery()
    )
    maxima = session.execute(
        select(*[func.max(getattr(grouped.c, f"d{index}")) for index in range(len(level_columns))])
    ).one()
    return [
        column for column, maximum in zip(level_columns, maxima)
        if maximum is not None and int(maximum) <= 1
    ]


def paired_columns(session: Session, spec: DatasetSpec) -> dict[str, list[str]]:
    """层级 → 可承接名称取值的列。

    一个名称列可能同时决定多个层级（定位单元名既决定定位单元编码也决定组织层级一编码），
    所以先按词根配对（bigareaname↔bigareacode），词根不匹配时归给它能决定的
    最细一层（shortname→oucode）。
    """
    cache_key = (spec.id, _registry_revision())
    cached = _PAIR_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    table = get_dynamic_table(spec)
    levels = _filterable_levels(spec)
    level_columns = [spec.level_value_key.get(level, level) for level in levels]
    mapping: dict[str, list[str]] = {level: [] for level in levels}
    for candidate in _candidate_columns(spec, table):
        determined = _determined_levels(session, table, candidate, level_columns)
        if not determined:
            continue
        stem = _stem(candidate, _NAME_SUFFIXES)
        owner = None
        if stem:
            for level, column in zip(levels, level_columns):
                if column in determined and _stem(column, _CODE_SUFFIXES) == stem:
                    owner = level
                    break
        if owner is None:
            indexes = [
                index for index, column in enumerate(level_columns) if column in determined
            ]
            if not indexes:
                continue
            owner = levels[max(indexes)]
        mapping[owner].append(candidate)
    for level, columns in mapping.items():
        physical = spec.level_value_key.get(level, level)
        stem = _stem(physical, _CODE_SUFFIXES)
        # 展示用哪个名称列：词根同源优先，其次列名里带 name 的。
        columns.sort(key=lambda name: (
            0 if stem and _stem(name, _NAME_SUFFIXES) == stem else 1,
            0 if "name" in name.casefold() else 1,
            name,
        ))
    _PAIR_CACHE[cache_key] = (time.monotonic(), mapping)
    return mapping


def _codes_for(session: Session, table, column: str, physical: str, terms: list[str], like: bool = False) -> list[str]:
    condition = table.c[column].contains(terms[0]) if like else table.c[column].in_(terms)
    rows = session.execute(
        select(func.distinct(table.c[physical]))
        .select_from(table)
        .where(condition, table.c[physical].isnot(None), table.c[physical] != "")
        .limit(200)
    ).all()
    return [str(row[0]) for row in rows if row[0] not in (None, "")]


def resolve_level_values(
    session: Session,
    spec: DatasetSpec,
    level: str,
    values: list[str],
) -> dict[str, Any]:
    """把某层级的请求取值编译成该层级列上真实可过滤的值。

    先在本列精确匹配，再走配对名称列（整值相等 → 包含回退）；落空的原样
    回传进 `unresolved`，由调用方如实报错，绝不猜。
    """
    wanted = [str(value).strip() for value in values if str(value).strip()]
    if not wanted:
        return {"codes": [], "matched_via": None, "unresolved": []}
    cache_key = (spec.id, _registry_revision(), level, ",".join(wanted))
    cached = _VALUE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    table = get_dynamic_table(spec)
    physical = spec.level_value_key.get(level, level)
    column = table.c.get(physical)
    if column is None:
        return {"codes": [], "matched_via": None, "unresolved": wanted}

    direct = set(_codes_for(session, table, physical, physical, wanted))
    resolved = {value for value in wanted if value in direct}
    codes = set(direct)
    via = "column" if direct else None
    # 本列先做一次包含回退，与 /levels/{level}/values 同口径（"示例区域丙"→"示例区域乙示例定位单元丙"）。
    remaining = [value for value in wanted if value not in resolved]
    for term in list(remaining):
        hits = _codes_for(session, table, physical, physical, [term], like=True)
        if hits:
            codes.update(hits)
            resolved.add(term)
            via = via or "column"
    remaining = [value for value in wanted if value not in resolved]
    # 编码已在目标列直接命中时无需扫描整张事实表推断「编码↔名称」函数依赖。
    # 日报几十万行时，这个无效扫描会让首次类别查询额外等待十几秒。
    pairs = paired_columns(session, spec).get(level, []) if remaining else []
    for candidate in pairs:
        if not remaining:
            break
        hits = _codes_for(session, table, candidate, physical, remaining)
        if not hits:
            continue
        matched_terms = [
            term for term in remaining
            if _codes_for(session, table, candidate, physical, [term])
        ]
        codes.update(hits)
        resolved.update(matched_terms)
        via = candidate
        remaining = [value for value in wanted if value not in resolved]
    for candidate in pairs:
        if not remaining:
            break
        for term in list(remaining):
            hits = _codes_for(session, table, candidate, physical, [term], like=True)
            if hits:
                codes.update(hits)
                resolved.add(term)
                via = candidate
                remaining = [value for value in wanted if value not in resolved]
    result = {
        "codes": sorted(codes),
        "matched_via": via,
        "unresolved": [value for value in wanted if value not in resolved],
    }
    _VALUE_CACHE[cache_key] = (time.monotonic(), result)
    return result


# 组织编码前缀桥：只认识"这一列是不是变长数字层级码"，不认识任何具体数据集。
_DIGITS = re.compile(r"^[0-9]+$")
_ORG_LEVEL_TOKENS = (
    "组织层级一", "组织层级三", "定位单元", "服务节点", "组织", "机构", "公司", "区域", "组织层级二",
    "org", "unit", "branch", "area", "station", "store", "region", "bigarea",
)
# 展开节点上限：超过它说明这个前缀几乎覆盖了整棵编码树，
# 与"条件被静默丢弃、答成全量"难以区分，宁可不桥。
_BRIDGE_MAX_NODES = 512

_CODE_INFO_CACHE: dict[tuple[str, int, str], tuple[float, dict[str, Any] | None]] = {}
_BRIDGE_CACHE: dict[tuple[str, int, str, str], tuple[float, dict[str, Any]]] = {}


def _is_org_level(spec: DatasetSpec, level: str) -> bool:
    """层级名/列名/中文名里带组织词，才允许参与编码桥。"""
    haystack = normalize_key(level) + normalize_key(spec.level_labels.get(level, level))
    haystack += normalize_key(spec.level_value_key.get(level, level))
    haystack += normalize_key(spec.column_sources.get(spec.level_value_key.get(level, level), ""))
    return any(normalize_key(token) in haystack for token in _ORG_LEVEL_TOKENS)


def _code_column_info(session: Session, spec: DatasetSpec, level: str) -> dict[str, Any] | None:
    """该层级的编码列是否为变长数字层级码，返回它的取值与宽度分布。"""
    physical = spec.level_value_key.get(level, level)
    cache_key = (spec.id, _registry_revision(), level)
    cached = _CODE_INFO_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    table = get_dynamic_table(spec)
    column = table.c.get(physical)
    info: dict[str, Any] | None = None
    if column is not None:
        values = [
            str(row[0]).strip()
            for row in session.execute(
                select(column).where(column.isnot(None), column != "").distinct().limit(20001)
            ).all()
            if str(row[0] or "").strip()
        ]
        if 1 < len(values) <= 20000 and all(_DIGITS.match(value) for value in values):
            widths = sorted({len(value) for value in values})
            if len(widths) > 1:
                info = {"physical": physical, "values": values, "widths": widths}
    _CODE_INFO_CACHE[cache_key] = (time.monotonic(), info)
    return info


def _name_column_for(spec: DatasetSpec, table, level: str) -> str | None:
    """该编码层级的名称列，只按列名词根配对（bigareacode↔bigareaname）。

    不用 `paired_columns`：它要对每个候选列做函数依赖分组扫描，定位单元级日报表上要
    20 秒，不能放进在线查询路径。词根配对只用注册声明，配不上就不参与桥。
    """
    physical = spec.level_value_key.get(level, level)
    stem = _stem(physical, _CODE_SUFFIXES)
    if not stem:
        return None
    level_columns = {spec.level_value_key.get(item, item) for item in spec.levels}
    metric_columns = {metric.column for metric in spec.metrics}
    for column in table.columns:
        if column.name in (physical, *level_columns, *metric_columns):
            continue
        if not isinstance(column.type, String):
            continue
        if _stem(column.name, _NAME_SUFFIXES) == stem:
            return column.name
    return None


def _source_org_pairs(source: DatasetSpec) -> list[tuple[str, str, str]]:
    """该数据集里可参与编码桥的 (层级, 名称列, 编码列) 三元组。"""
    table = get_dynamic_table(source)
    pairs: list[tuple[str, str, str]] = []
    for level in _filterable_levels(source):
        if not _is_org_level(source, level):
            continue
        name_column = _name_column_for(source, table, level)
        if not name_column:
            continue
        pairs.append((level, name_column, source.level_value_key.get(level, level)))
    return pairs


def _codes_for_names(
    session: Session, table, pairs: list[tuple[str, str, str]], wanted: list[str]
) -> dict[str, dict[str, Any]]:
    """一张事实表只扫一次：任一名称列命中提问用词时，取回同行的组织编码。"""
    columns: list[str] = []
    for _, name_column, code_column in pairs:
        columns.extend([name_column, code_column])
    columns = list(dict.fromkeys(columns))
    conditions = [
        table.c[name_column].like(f"%{term}%")
        for _, name_column, _ in pairs
        for term in wanted
    ]
    rows = session.execute(
        select(*[table.c[column] for column in columns])
        .where(or_(*conditions))
        .distinct()
        .limit(2000)
    ).all()
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        values = {column: str(getattr(row, column) or "").strip() for column in columns}
        for level, name_column, code_column in pairs:
            name_value = values.get(name_column, "")
            if not any(term in name_value for term in wanted):
                continue
            code = values.get(code_column, "")
            if not code or code in found:
                continue
            found[code] = {"level": level, "matched_via": name_column, "matched_name": name_value}
    return found


def _bridge_org_codes_uncached(
    session: Session,
    target: DatasetSpec,
    level: str,
    wanted: list[str],
) -> dict[str, Any]:
    """跨数据集的组织编码前缀桥：把本数据集编码列答不上的组织名换成本列真实节点。

    计划表这类数据集只有变长层级的组织编码（示例区域甲下辖定位单元是 15 位码），没有能
    承接"示例区域甲组织层级一"的名称列，本地取值检索永远落空。这里用其它数据集真实存在
    的"名称→编码"解析出组织编码，再在本列按前缀展开成它真实存在的节点集合。

    只在本地精确/包含解析落空后兜底。候选编码去掉被更粗前缀覆盖的重复（"示例区域甲"
    同时命中组织层级一码和它下辖的组织层级二/组织层级三码时取组织层级一码），展开后必须仍是本列真实
    取值、且不是整棵编码树——桥成功后条件等价于"不过滤"时，与静默丢条件无法
    区分，宁可不桥。取值全部来自真查，不建别名表。
    """
    empty = {"codes": [], "evidence": [], "unresolved": wanted}
    info = _code_column_info(session, target, level)
    if not info or not wanted:
        return empty
    values = info["values"]

    candidates: dict[str, dict[str, Any]] = {}
    for source in sorted(registry.specs.values(), key=lambda item: item.id):
        if source.id == target.id or source.kind == "dimension":
            continue
        pairs = _source_org_pairs(source)
        if not pairs:
            continue
        found = _codes_for_names(session, get_dynamic_table(source), pairs, wanted)
        for code, hit in found.items():
            if not _DIGITS.match(code) or code in candidates:
                continue
            candidates[code] = {
                "bridge_code": code,
                "source_dataset": source.id,
                "source_label": source.label,
                "source_level": hit["level"],
                "matched_via": hit["matched_via"],
                "matched_name": hit["matched_name"],
                "terms": list(wanted),
            }
    if not candidates:
        return empty

    # 只保留互不包含的最粗一层：'示例区域甲' 同时解析出组织层级一码和它下辖的组织层级三/定位单元码时，
    # 定位单元码是组织层级一码的子集，保留组织层级一码即可（前缀展开天然覆盖下级）。
    kept = [
        code for code in candidates
        if not any(other != code and code.startswith(other) for other in candidates)
    ]
    codes: list[str] = []
    evidence: list[dict[str, Any]] = []
    for code in sorted(kept):
        expanded = sorted(value for value in values if value == code or value.startswith(code))
        if len(expanded) > _BRIDGE_MAX_NODES or len(expanded) >= len(values):
            # 展开等于整棵编码树时，"桥成功"和"条件被丢掉"给出同一个结果，
            # 无法区分就不桥（与 X34/#42 同一个原则：宁缺不扩）。
            continue
        codes.extend(expanded)
        evidence.append({**candidates[code], "expanded_nodes": len(expanded)})
    if not codes:
        return {"codes": [], "evidence": [], "unresolved": wanted}
    return {"codes": sorted(set(codes)), "evidence": evidence, "unresolved": []}


def bridge_org_codes(
    session: Session,
    target: DatasetSpec,
    level: str,
    terms: list[str],
) -> dict[str, Any]:
    """编码桥要扫若干张事实表的名称列，同一次提问会被多处调用，按修订号缓存。"""
    wanted = [str(term).strip() for term in terms if str(term).strip()]
    if not wanted:
        return {"codes": [], "evidence": [], "unresolved": []}
    cache_key = (target.id, _registry_revision(), level, ",".join(sorted(wanted)))
    cached = _BRIDGE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    result = _bridge_org_codes_uncached(session, target, level, wanted)
    _BRIDGE_CACHE[cache_key] = (time.monotonic(), result)
    return result


def display_map(session: Session, spec: DatasetSpec, level: str, codes: list[str]) -> dict[str, str]:
    """编码 → 名称的展示映射；无配对列或反向不唯一时回落显示编码。"""
    if normalize_key(level) in TIME_LEVELS or not codes:
        return {}
    table = get_dynamic_table(spec)
    physical = spec.level_value_key.get(level, level)
    # 组织编码列通常有同词根的名称列（bigareacode ↔
    # bigareaname）。优先使用注册表列名即可确定的配对，避免在
    # 每次复合分组查询后重新扫描整张宽表做函数依赖推断。
    name_column = _name_column_for(spec, table, level)
    if not name_column:
        pairs = paired_columns(session, spec).get(level, [])
        if not pairs:
            return {}
        name_column = pairs[0]
    rows = session.execute(
        select(table.c[physical], func.max(table.c[name_column]))
        .select_from(table)
        .where(table.c[physical].in_(codes))
        .group_by(table.c[physical])
    ).all()
    return {
        str(code): str(name)
        for code, name in rows
        if code is not None and name not in (None, "")
    }


def level_by_key_hint(spec: DatasetSpec, key: str) -> str | None:
    """把调用方的语义槽键解析成该数据集的真实层级。

    两条运行时规则，都不查数据集专属词表：

    1. 词根前缀：`big_area`→`bigareacode`、`gas_code`→`gascode`；
    2. 中文维度词对注册表声明的层级中文名做包含匹配：组织层级一→组织层级一编码、
       定位单元→定位单元组织编码。命中多层时取最细的一层（"各产品"要的是产品编码）。

    有歧义或匹配不上时返回 None，让调用方显式报错而不是猜一个层级。
    """
    normalized = normalize_key(key)
    if not normalized:
        return None
    hits = [
        level for level in spec.levels
        if normalize_key(level).startswith(normalized) or normalized.startswith(normalize_key(level))
    ]
    if len(hits) == 1:
        return hits[0]
    words = SLOT_WORDS.get(str(key).strip().casefold())
    if words:
        labelled = [
            (index, level) for index, level in enumerate(spec.levels)
            if any(word in str(spec.level_labels.get(level, level)) for word in words)
        ]
        if labelled:
            return max(labelled, key=lambda item: item[0])[1]
    return hits[0] if len(set(hits)) == 1 else None


def resolve_condition_keys(
    spec: DatasetSpec,
    conditions: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """条件键归一：能落到层级或物理列的留下，落不了的原样回传。"""
    table = get_dynamic_table(spec)
    by_normalized_column = {
        normalize_key(column.name): column.name for column in table.columns
    }
    resolved: dict[str, Any] = {}
    unmapped: list[str] = []
    for key, value in conditions.items():
        if value in (None, "", []):
            continue
        if key in {"date_from", "date_to"} or key in spec.levels:
            resolved[key] = value
            continue
        hinted = level_by_key_hint(spec, key)
        if hinted is not None:
            resolved[hinted] = value
            continue
        physical = spec.level_value_key.get(key, key)
        if table.c.get(physical) is not None:
            resolved[key] = value
            continue
        normalized = by_normalized_column.get(normalize_key(key))
        if normalized is not None and table.c.get(normalized) is not None:
            resolved[normalized] = value
        else:
            unmapped.append(key)
    return resolved, unmapped


def resolve_group_by(spec: DatasetSpec, group_by: list[str]) -> tuple[list[str], list[str]]:
    """分组维度同样要显式：未知 group_by 静默退回上层会答成总额（X18）。"""
    resolved: list[str] = []
    unmapped: list[str] = []
    for key in group_by:
        if key in spec.levels:
            if key not in resolved:
                resolved.append(key)
            continue
        hinted = level_by_key_hint(spec, key)
        if hinted is not None:
            if hinted not in resolved:
                resolved.append(hinted)
        else:
            unmapped.append(key)
    return resolved, unmapped


def level_hint(spec: DatasetSpec, limit: int = 12) -> str:
    """报错时告诉调用方这个数据集到底有哪些层级可用（含中文名）。

    宽表数据集可以声明上百个层级，全量罗列会把答案淹没，所以只给前若干层，
    完整清单指向数据集声明页。
    """
    labels = [
        f"{level}({spec.level_labels.get(level, level)})" for level in spec.levels
    ]
    if not labels:
        return "无"
    if len(labels) <= limit:
        return "、".join(labels)
    return (
        "、".join(labels[:limit])
        + f" 等 {len(labels)} 层（完整清单见「数据集管理 → 编辑声明」）"
    )
