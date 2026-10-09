# -*- coding: utf-8 -*-
"""通用数据基座：把用户语义筛选翻译为目标数据集的层级条件。

基座不是某一个固定的产品表。任意注册数据集都可以声明为 base 数据源，
并通过 AI/人工确认的 semantic_type 提供产品、组织等基础映射。
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from engine.query.experience import base_semantics
from engine.storage.dataset_registry import DatasetSpec, registry
from engine.storage.fact_tables import get_dim_source, get_fact_source
from engine.storage.dynamic_table import get_dynamic_table


_PRODUCT_KEYS = ("product", "product_name", "product_code", "gas", "gas_code", "oil", "category", "type", "产品", "产品", "商品", "物料", "类别", "分类", "品类")
_ORG_KEYS = (
    "org", "organization", "org_name", "org_code", "branch", "station",
    "store", "location", "region", "province", "city", "county", "area",
    "地点", "地区", "区域", "省", "市", "县", "组织", "机构", "公司", "定位单元",
)


def _experience_examples(section: str) -> set[str]:
    """读取 YAML 基座经验中的自然语言示例，作为通用语义别名补充。"""
    raw = base_semantics().get("semantic_translation") or {}
    item = raw.get(section) if isinstance(raw, dict) else None
    examples = item.get("examples") if isinstance(item, dict) else None
    return {_norm(value) for value in (examples or []) if _norm(value)}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: Any) -> str:
    return _text(value).lower().replace("_", "").replace("-", "")


def _code_domain_key(value: Any) -> str:
    """编码域的归一键：纯数字编码去掉前导零后同域（`01` 与 `1` 是同一大类）。

    判定"两列是不是同一编码域"必须逐字比较取值，但同一含义的编码在不同表里
    常差一个补零（主数据 TypeCode=`1`，事实表 Category=`01`），逐字比较会让
    大类层永远不相通。只对全数字值归一，`92#`、`150799` 这类原样保留。
    """
    text = _text(value)
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text.lower()


def _row_mapping(row: Any) -> dict[str, Any]:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return {str(k): v for k, v in mapping.items()}
    return {
        str(key): getattr(row, key, None)
        for key in dir(row)
        if not key.startswith("_") and not callable(getattr(row, key, None))
    }


def _base_rows(session: Session, spec: DatasetSpec) -> list[dict[str, Any]]:
    source = get_dim_source(spec.id) if spec.kind == "dimension" else get_fact_source(spec.id)
    return [_row_mapping(row) for row in source.all_rows(session)]


def _feature_physical_name(spec: DatasetSpec, feature: dict[str, Any]) -> str:
    source = str(feature.get("source") or "")
    name = str(feature.get("name") or "")
    if name:
        return name
    if spec.kind == "dimension":
        for column in spec.columns:
            if column.source == source:
                return column.name
    for physical, declared_source in spec.column_sources.items():
        if declared_source == source or physical == source:
            return physical
    return source


def _requested_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def _feature_matches_key(feature: dict[str, Any], key: str) -> bool:
    key_norm = _norm(key)
    if not key_norm:
        return False
    semantic = _norm(feature.get("semantic_type"))
    names = {
        _norm(feature.get("source")),
        _norm(feature.get("name")),
        _norm(feature.get("label")),
        semantic,
    }
    if key_norm in names:
        return True
    if any(alias in key_norm for alias in _PRODUCT_KEYS) or key_norm in _experience_examples("product_alias"):
        return semantic in {"productcode", "productname", "alias", "category"}
    if any(alias in key_norm for alias in _ORG_KEYS):
        return semantic in {"orgcode", "orgname"}
    return False


def _row_matches(row: dict[str, Any], features: list[dict[str, Any]], key: str, values: list[str], spec: DatasetSpec) -> bool:
    candidates = [feature for feature in features if _feature_matches_key(feature, key)]
    if not candidates:
        return False
    for feature in candidates:
        physical = _feature_physical_name(spec, feature)
        actual = _text(row.get(physical))
        if not actual:
            continue
        if any(wanted == actual or wanted.lower() in actual.lower() for wanted in values):
            return True
    return False


def _level_is(spec: DatasetSpec, level: str, wanted: Iterable[str]) -> bool:
    key = _norm(level)
    value_key = _norm(spec.level_value_key.get(level, level))
    source = _norm(spec.column_sources.get(spec.level_value_key.get(level, level), ""))
    label = _norm(spec.level_labels.get(level, level))
    return any(
        token.replace("_", "") in key
        or token.replace("_", "") in value_key
        or token.replace("_", "") in source
        or token.replace("_", "") in label
        for token in wanted
    )


def _wanted_keys(semantic: str) -> tuple[str, ...]:
    if semantic.startswith("product") or semantic in {"alias", "category"}:
        return _PRODUCT_KEYS
    return _ORG_KEYS


def _target_level(spec: DatasetSpec, semantic: str) -> str | None:
    wanted = _wanted_keys(semantic)
    for level in spec.levels:
        if _level_is(spec, level, wanted):
            return level
    return None


def _target_levels(spec: DatasetSpec, semantic: str) -> list[str]:
    """目标数据集上可承接该语义的全部层级，按「最细层优先」返回。

    产品语义可能落在类别层（01=类别甲）也可能落在产品编码层，选哪一层不能靠
    名字猜，必须由事实表里的真实取值决定；这里只给出候选顺序。
    """
    wanted = _wanted_keys(semantic)
    return [level for level in reversed(spec.levels) if _level_is(spec, level, wanted)]


def _declared_features(spec: DatasetSpec) -> list[dict[str, Any]]:
    """返回数据集确认过的特征计划；没有计划时只使用已声明的基座特征。"""
    raw = spec.capabilities.get("feature_layers")
    if isinstance(raw, dict):
        result: list[dict[str, Any]] = []
        for layer in ("base", "trunk", "branch"):
            for item in raw.get(layer) or []:
                if isinstance(item, dict) and item.get("source"):
                    result.append(item)
        if result:
            return result
    return [item for item in (spec.base.get("features") or [])
            if isinstance(item, dict) and item.get("source")]


def _feature_level(
    spec: DatasetSpec,
    feature: dict[str, Any],
    session: Session | None = None,
) -> str | None:
    """把已确认特征映射回目标数据集的可路由层级。"""
    names = {
        _norm(feature.get("source")),
        _norm(feature.get("name")),
        _norm(feature.get("label")),
    }
    for level in spec.levels:
        value_key = spec.level_value_key.get(level, level)
        physical = spec.column_sources.get(value_key, value_key)
        if names & {_norm(level), _norm(value_key), _norm(physical)}:
            return level
    if session is None:
        return None
    # 名称列常常不是层级（产品事实表把 ShortName/AreaName 降成了附加事实列）。
    # 此时用事实表自身的函数依赖把它归到"它能唯一决定"的最细一层，
    # 依旧不建别名表、不写死列名。
    from engine.query.level_resolution import paired_columns

    physical = _norm(_feature_physical_name(spec, feature))
    pairs = paired_columns(session, spec)
    candidates = [
        level for level, columns in pairs.items()
        if physical in {_norm(column) for column in columns} and level in spec.levels
    ]
    if not candidates:
        return None
    return max(candidates, key=spec.levels.index)


def _semantic_for_key(key: str) -> str | None:
    key_norm = _norm(key)
    if any(token.replace("_", "") in key_norm for token in _PRODUCT_KEYS) or key_norm in _experience_examples("product_alias"):
        return "product"
    if any(token.replace("_", "") in key_norm for token in _ORG_KEYS):
        return "org"
    return None


def _distinct_feature_values(
    session: Session,
    spec: DatasetSpec,
    physical: str,
    values: list[str],
) -> list[str]:
    """在目标事实表中按声明字段检索实际组织值，不加载全表。"""
    if spec.kind != "generic":
        source = get_dim_source(spec.id)
        column = source.table.c.get(physical)
    else:
        column = get_dynamic_table(spec).c.get(physical)
    if column is None or not values:
        return []
    expressions = [cast(column, String).like(f"%{value}%") for value in values]
    statement = (
        select(column)
        .where(column.is_not(None), or_(*expressions))
        .distinct()
        .limit(128)
    )
    rows = session.execute(statement).all()
    return [str(row[0]).strip() for row in rows if str(row[0] or "").strip()]


def _location_candidate_score(
    feature: dict[str, Any],
    matched_values: list[str],
) -> int:
    """优先选择组织公司级字段，避免把地点误解析成定位单元名称集合。"""
    name = _norm(feature.get("source") or feature.get("name"))
    score = max(0, 20 - len(matched_values))
    if any(token in name for token in ("ou", "unit", "company", "bigarea")):
        score += 50
    if any(str(value).endswith(("组织层级一", "公司")) for value in matched_values):
        score += 20
    if any(token in name for token in ("province", "city", "county", "area")):
        score += 10
    return score


def _bridge_probe_hits(
    session: Session, spec: DatasetSpec, term: str
) -> list[dict[str, Any]]:
    """探测用的编码桥命中：只报桥到的节点码与展开规模，不铺开全部下级。"""
    level = _target_level(spec, "org")
    if not level:
        return []
    from engine.query.level_resolution import bridge_org_codes

    result = bridge_org_codes(session, spec, level, [term])
    if not result["codes"]:
        return []
    physical = spec.level_value_key.get(level, level)
    return [{
        "dataset": spec.id,
        "feature_source": physical,
        "feature_name": physical,
        "semantic_type": "org_code",
        "resolution": "org_code_bridge",
        "values": [item["bridge_code"] for item in result["evidence"]],
        "expanded_nodes": len(result["codes"]),
        "bridge": [
            {key: item.get(key) for key in (
                "bridge_code", "matched_name", "matched_via",
                "source_dataset", "source_label", "source_level",
            )}
            for item in result["evidence"]
        ],
    }]


def probe_semantic_value(
    session: Session, spec: DatasetSpec, term: str
) -> dict[str, Any]:
    """判断一个自然语言词是否真实存在于该数据集声明的组织/产品列中。

    Agent 在把地点、品类之类的基座过滤条件下发给引擎之前先调用它自检：
    候选值一律来自数据集自己的列，既不需要静态别名表，也不会让 Agent 猜值。
    """
    wanted = _text(term)
    hits: list[dict[str, Any]] = []
    configured = 0
    if not wanted:
        return {"dataset": spec.id, "term": wanted, "matched": False,
                "configured": False, "hits": []}
    from engine.query.foundation_config import effective_base_features

    # 与 compile_base_filters 同口径：目标数据集自己的特征列 + 已启用的共享基座
    scopes: list[tuple[DatasetSpec, list[dict[str, Any]]]] = [
        (spec, _declared_features(spec))
    ]
    for base_spec in registry.specs.values():
        if base_spec.id == spec.id or not bool((base_spec.base or {}).get("enabled")):
            continue
        scopes.append((base_spec, effective_base_features(base_spec.id)))
    for owner, features in scopes:
        for feature in features:
            semantic = str(feature.get("semantic_type") or "")
            if semantic not in {"org_name", "org_code", "product_name", "product_code"}:
                continue
            configured += 1
            physical = _feature_physical_name(owner, feature)
            values = _distinct_feature_values(session, owner, physical, [wanted])
            if values:
                hits.append({
                    "dataset": owner.id,
                    "feature_source": feature.get("source"),
                    "feature_name": feature.get("name"),
                    "semantic_type": semantic,
                    "values": values[:20],
                })
    # configured=False 表示根本没有可承接地点/品类的已声明特征列：
    # 此时无法判断该词真伪，Agent 要提示"缺基座特征"而不是"值不存在"。
    if not hits:
        # 与 compile_base_filters 同口径：本数据集名称列答不上时，编码前缀桥可能
        # 能把它落到本列真实节点。探测不认桥，Agent 就会把引擎其实能执行的条件
        # 提前丢掉（丢掉组织条件等于把组织层级一口径答成全公司口径）。
        hits = _bridge_probe_hits(session, spec, wanted)
    return {"dataset": spec.id, "term": wanted, "matched": bool(hits),
            "configured": configured > 0 or bool(hits), "hits": hits}


def _bridge_org_condition(
    session: Session,
    target: DatasetSpec,
    values: list[str],
) -> tuple[str, list[str], list[dict[str, Any]]] | None:
    """本数据集的组织列答不上时，试一次跨数据集编码前缀桥。

    返回 `(层级, 本列真实节点取值, 桥依据)`；桥不成返回 None，让调用方按
    "取值不存在"如实报错——绝不因为"找不到"就丢掉组织条件。
    """
    level = _target_level(target, "org")
    if not level:
        return None
    from engine.query.level_resolution import bridge_org_codes

    result = bridge_org_codes(session, target, level, values)
    if not result["codes"]:
        return None
    return level, result["codes"], result["evidence"]


def _compile_dataset_semantic_filters(
    session: Session,
    target: DatasetSpec,
    requested: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    """把通用地点/组织语义解析为目标数据集自己的物理层级。

    第三个返回值是"本数据集有基座声明可以承接"的条件键：有声明但取值对不上
    与压根没声明是两种不同的失败，报错文案必须区分，否则会把"我查不到这个值"
    说成"我不认识这个条件"。
    """
    conditions: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    features = _declared_features(target)
    supported: set[str] = set()
    for key, value in requested.items():
        semantic = _semantic_for_key(key)
        if semantic != "org":
            continue
        values = _requested_values(value)
        candidates: list[tuple[int, dict[str, Any], str, list[str]]] = []
        for feature in features:
            if str(feature.get("semantic_type") or "") not in {"org_name", "org_code"}:
                continue
            level = _feature_level(target, feature, session)
            if not level:
                continue
            supported.add(key)
            physical = _feature_physical_name(target, feature)
            matches = _distinct_feature_values(session, target, physical, values)
            if not matches:
                continue
            score = _location_candidate_score(feature, matches)
            candidates.append((score, feature, level, matches))
        if not candidates:
            bridged = _bridge_org_condition(session, target, values)
            if bridged is None:
                continue
            level, matches, bridge_evidence = bridged
            supported.add(key)
            conditions[level] = matches[0] if len(matches) == 1 else matches
            evidence.append({
                "semantic": semantic,
                "requested_key": key,
                "requested_value": value,
                "feature_source": target.level_value_key.get(level, level),
                "feature_name": target.level_value_key.get(level, level),
                "level": level,
                "resolved_values": [item["bridge_code"] for item in bridge_evidence],
                "resolved_nodes": len(matches),
                "resolution": "org_code_bridge",
                "bridge": bridge_evidence,
            })
            continue
        _, feature, level, matches = max(candidates, key=lambda item: item[0])
        conditions[level] = matches[0] if len(matches) == 1 else matches
        evidence.append({
            "semantic": semantic,
            "requested_key": key,
            "requested_value": value,
            "feature_source": feature.get("source"),
            "feature_name": feature.get("name"),
            "level": level,
            "resolved_values": matches,
        })
    return conditions, {"conditions": conditions, "evidence": evidence}, supported


def rehost_org_conditions(
    session: Session,
    target: DatasetSpec,
    requested: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    """按基座声明给组织语义条件找真实层级。

    调用方槽名（`station`、`store` 之类）在英文列名数据集上可能匹配不到任何
    层级，但数据集自己声明的 `org_name`/`org_code` 特征列能承接同一个值。
    候选层只来自声明、取值只来自事实表真查，不建别名表。

    返回 `(层级条件, 决策依据, 有声明可承接的键)`，与基座过滤同口径。
    """
    conditions, evidence, supported = _compile_dataset_semantic_filters(
        session, target, requested
    )
    return conditions, list(evidence.get("evidence") or []), supported


def _distinct_column_values(
    session: Session,
    spec: DatasetSpec,
    physical: str,
) -> list[str]:
    """某张表某条特征列的全部真实取值（用于判定列与层是不是同一编码域）。"""
    column = (
        get_dynamic_table(spec).c.get(physical)
        if spec.kind == "generic" else get_dim_source(spec.id).table.c.get(physical)
    )
    if column is None:
        return []
    statement = select(column).where(column.is_not(None)).distinct().limit(4096)
    return sorted({_text(row[0]) for row in session.execute(statement)} - {""})


def _resolve_product_condition(
    session: Session,
    base_spec: DatasetSpec,
    target: DatasetSpec,
    code_features: list[dict[str, Any]],
    matched: list[dict[str, Any]],
    resolution: str = "product_value_intersection",
) -> tuple[str, list[str], dict[str, Any]] | None:
    """把基座命中的产品取值落到目标数据集真实存在的那一层。

    基座可以同时声明多条产品编码列（物料分类编码、物料编码、大类编码……），目标数据集也
    可以同时有类别层与产品编码层。用哪一列、落哪一层一律由事实表真查决定，不建
    「MaterialClass→category」这类映射表。

    判定标准是**整列编码域是否相通**，不是本次查询碰巧相交：计划表的 gascode 里
    混着 `0`、`150799` 这类分类码，单看一次提问能和物料分类编码列对得上，但整列
    相关性远低于物料编码列，选前者会把「类别乙」筛成两条杂码。没有相通证据就返回
    None，让上层按「取值不存在」显式失败，而不是拼出一个查不到数的条件。
    """
    levels = _target_levels(target, "product")
    # 归一后的事实列取值表：键是归一键，值是**事实表里真实存在的那个写法**，
    # 编译条件必须下发后者（`01` 而不是 `1`），否则层条件对不上任何行。
    domains = {
        level: {
            _code_domain_key(value): value
            for value in _distinct_column_values(
                session, target, target.level_value_key.get(level, level)
            )
        }
        for level in levels
    }
    candidates: list[tuple[int, int, int, str, str, list[str]]] = []
    for feature_order, feature in enumerate(code_features):
        physical = _feature_physical_name(base_spec, feature)
        domain_keys = {
            _code_domain_key(value)
            for value in _distinct_column_values(session, base_spec, physical)
        }
        matched_keys = {
            _code_domain_key(value)
            for value in (_text(row.get(physical)) for row in matched) if value
        }
        if not domain_keys or not matched_keys:
            continue
        for level_order, level in enumerate(levels):
            by_key = domains[level]
            present = domain_keys & set(by_key)
            hits = sorted({by_key[key] for key in present & matched_keys})
            if not hits:
                continue
            candidates.append(
                (len(present), -level_order, -feature_order, physical, level, hits)
            )
    if not candidates:
        return None
    affinity, _, _, physical, level, hits = max(candidates)
    return level, hits, {
        "base_dataset": base_spec.id,
        "base_feature": physical,
        "level": level,
        "resolved_values": hits,
        "resolved_nodes": len(hits),
        "domain_overlap": affinity,
        "resolution": resolution,
    }


def _domain_code_width(domain: list[str]) -> int | None:
    """一条编码列的「典型宽度」= 归一后位数众数；不是纯数字编码列时返回 None。

    计划表 gascode 里混着 `0`、`01`、`03`、`150799` 这类从别的列串进来的杂码，
    整列仍是 8 位物料编码域。众数把"这一列长什么样"说清楚，杂码不会因为它
    改变这一层的编码域判定。
    """
    widths = [
        len(key) for key in (_code_domain_key(value) for value in domain)
        if key.isdigit()
    ]
    if not widths or len(widths) * 2 < len(domain):
        return None
    counts: dict[int, int] = {}
    for width in widths:
        counts[width] = counts.get(width, 0) + 1
    return max(counts, key=lambda width: (counts[width], -width))


def _typical_in_domain(values: list[str], domain: list[str]) -> bool:
    """请求值像不像这一层的编码：宽度与整列众数不同就不像。"""
    width = _domain_code_width(domain)
    if width is None:
        return True
    keys = [_code_domain_key(value) for value in values]
    return all(len(key) == width for key in keys if key.isdigit())


def _verbatim_product_condition(
    session: Session,
    target: DatasetSpec,
    values: list[str],
) -> tuple[str, list[str], dict[str, Any]] | None:
    """请求值本身就是目标某产品层的真实取值时，直接按那一层过滤，不做主数据展开。

    主数据展开是为「名字→编码」准备的。已经是编码的值再喂给相交，等于让子串匹配
    去猜层——`01` 会命中物料编码里含 `01` 的行，再被整列相交扩成两个别的产品编码，
    与事实表自己的大类条件相与后**静默筛成空集且不报错**（X41①）。全部请求值都在
    同一层逐字存在才算数，部分命中宁可不接管，交给展开或取值校验。

    同一短码可能在两层都逐字存在（计划表 `01` 既是大类取值也是串进来的杂码产品编码），
    这时按宽度众数挑"这一层真的长这样"的那一条：落错层就是 231 行 vs 80615 行。
    """
    wanted = {_text(value) for value in values if _text(value)}
    if not wanted:
        return None
    candidates: list[tuple[str, str, list[str], bool]] = []
    for level in _target_levels(target, "product"):
        column = target.level_value_key.get(level, level)
        domain = _distinct_column_values(session, target, column)
        present = sorted(wanted & set(domain))
        if len(present) == len(wanted):
            candidates.append((level, column, present, _typical_in_domain(present, domain)))
    if not candidates:
        return None
    level, column, present, _ = next(
        (item for item in candidates if item[3]), candidates[0]
    )
    return level, present, {
        "base_dataset": None,
        "base_feature": None,
        "fact_column": column,
        "level": level,
        "resolved_values": present,
        "resolved_nodes": len(present),
        "domain_overlap": len(present),
        "resolution": "verbatim_fact_value",
    }


def _category_term_condition(
    session: Session,
    base_spec: DatasetSpec,
    target: DatasetSpec,
    features: list[dict[str, Any]],
    semantic_keys: dict[str, Any],
    matched: list[dict[str, Any]],
) -> tuple[str, list[str], dict[str, Any]] | None:
    """提问词命中的是基座大类名/大类码时，落到数据集自己标注的大类层。

    「类别甲」既能按大类码落一层，也能展开成一串物料编码。展开只对"编码列里真实存在
    的那几个产品"成立：计划表里有一批 `category=01` 而 `gascode` 不是物料编码的行，
    按编码展开会把它们筛掉（同一句「类别甲」在月报与自身大类完全等价，在计划表少 231
    行 / 110,844.95 元）。大类标记是数据集自己给的口径，命中大类词时应当优先；而
    「类别代码甲」这类不属于任何大类名的说法仍然只走编码展开，不会被摊成一整个品类。
    """
    category_features = [
        feature for feature in features
        if str(feature.get("semantic_type") or "") == "category"
    ]
    if not category_features:
        return None
    for key, value in semantic_keys.items():
        terms = _requested_values(value)
        if not terms:
            continue
        if not any(
            _row_matches(row, category_features, key, terms, base_spec)
            for row in matched
        ):
            continue
        return _resolve_product_condition(
            session, base_spec, target, category_features, matched,
            "category_value_intersection",
        )
    return None


def compile_base_filters(
    session: Session,
    target_dataset: str,
    base_filters: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回目标层级条件与可审计的基座决策。"""
    requested = base_filters or {}
    decision: dict[str, Any] = {
        "enabled": bool(requested),
        "filters": requested,
        "sources": [],
        "matched_rows": 0,
        "compiled_conditions": {},
        "display_conditions": {},
        "warnings": [],
        "blocked": False,
        "status": "not_requested" if not requested else "pending",
    }
    if not requested:
        return {}, decision

    target = registry.require(target_dataset)
    conditions: dict[str, Any] = {}
    from engine.query.foundation_config import effective_base_features

    declared_keys: set[str] = set()
    for base_spec in registry.specs.values():
        if base_spec.id == target_dataset or not bool((base_spec.base or {}).get("enabled")):
            continue
        features = effective_base_features(base_spec.id)
        if not features:
            features = [f for f in ((base_spec.base or {}).get("features") or []) if isinstance(f, dict)]
        if not features:
            continue
        supported = {
            key: value for key, value in requested.items()
            if any(_feature_matches_key(feature, key) for feature in features)
        }
        if not supported:
            continue
        declared_keys.update(supported)
        rows = _base_rows(session, base_spec)
        matched = [
            row for row in rows
            if all(_row_matches(row, features, key, _requested_values(value), base_spec) for key, value in supported.items())
        ]
        if not matched:
            continue
        decision["sources"].append({
            "dataset": base_spec.id,
            "label": base_spec.label,
            "scope": "shared_base",
        })
        decision["matched_rows"] += len(matched)
        for semantic in ("product", "org"):
            semantic_keys = {
                key: value for key, value in supported.items()
                if _semantic_for_key(key) == semantic
            }
            if not semantic_keys:
                continue
            semantic_type = "product_code" if semantic == "product" else "org_code"
            code_features = [
                f for f in features
                if str(f.get("semantic_type") or "") == semantic_type
            ]
            if not code_features:
                decision["warnings"].append(
                    f"基座数据集 {base_spec.id} 未声明 {semantic_type} 特征"
                )
                continue
            level = _target_level(target, semantic)
            if not level:
                decision["warnings"].append(
                    f"目标数据集 {target_dataset} 没有可承接 {semantic_type} 的层级"
                )
                continue
            if semantic == "product":
                requested_terms = [
                    value for item in semantic_keys.values()
                    for value in _requested_values(item)
                ]
                resolved = (
                    _verbatim_product_condition(session, target, requested_terms)
                    or _category_term_condition(
                        session, base_spec, target, features, semantic_keys, matched
                    )
                    or _resolve_product_condition(
                        session, base_spec, target, code_features, matched
                    )
                )
                if resolved is not None:
                    level, values, evidence = resolved
                    evidence = dict(evidence)
                    evidence["requested_terms"] = requested_terms
                    value_labels: dict[str, str] = {}
                    resolved_by_key = {
                        _code_domain_key(value): value for value in values
                    }
                    physical = str(evidence.get("base_feature") or "")
                    if physical:
                        for key, requested_value in semantic_keys.items():
                            for term in _requested_values(requested_value):
                                term_rows = [
                                    row for row in matched
                                    if _row_matches(
                                        row, features, key, [term], base_spec
                                    )
                                ]
                                for row in term_rows:
                                    resolved_value = resolved_by_key.get(
                                        _code_domain_key(_text(row.get(physical)))
                                    )
                                    if resolved_value:
                                        value_labels[resolved_value] = term
                    else:
                        for term in requested_terms:
                            resolved_value = resolved_by_key.get(
                                _code_domain_key(term)
                            )
                            if resolved_value:
                                value_labels[resolved_value] = term
                    decision.setdefault("product_resolution", []).append(evidence)
                    conditions[level] = values if len(values) > 1 else values[0]
                    decision["compiled_conditions"][level] = conditions[level]
                    decision["display_conditions"][level] = {
                        "labels": requested_terms,
                        "compiled_values": values,
                        "value_labels": value_labels,
                    }
                    continue
                decision["warnings"].append(
                    f"基座 {base_spec.id} 的产品编码在 {target_dataset} 真实取值中没有相交层"
                )
            physical = _feature_physical_name(base_spec, code_features[0])
            values = sorted({
                _text(row.get(physical)) for row in matched if _text(row.get(physical))
            })
            if values:
                conditions[level] = values if len(values) > 1 else values[0]
                decision["compiled_conditions"][level] = conditions[level]

    local_conditions, local_evidence, local_supported = _compile_dataset_semantic_filters(
        session, target, requested
    )
    declared_keys.update(local_supported)
    for key, value in local_conditions.items():
        if key not in conditions or conditions[key] in (None, "", []):
            conditions[key] = value
        else:
            existing = _requested_values(conditions[key])
            incoming = _requested_values(value)
            merged = sorted(set(existing).intersection(incoming))
            conditions[key] = merged[0] if len(merged) == 1 else merged
        decision["compiled_conditions"][key] = conditions[key]
    if local_evidence.get("evidence"):
        decision["sources"].append({
            "dataset": target_dataset,
            "label": target.label,
            "scope": "dataset_semantic",
            "evidence": local_evidence["evidence"],
        })

    if requested and not decision["sources"]:
        decision["blocked"] = True
        decision["status"] = "blocked"
        # 分两种失败：数据集根本没有可承接该语义的基座声明，和有基座但取值
        # 对不上。两者都只影响本数据集，不能让它对外宣称"该期间没有数据"。
        decision["reason"] = "no_matching_value" if declared_keys else "no_base_declaration"
        terms = "、".join(
            value
            for values in (_requested_values(v) for _, v in sorted(requested.items()))
            for value in values
        )
        if decision["reason"] == "no_matching_value":
            message = (
                f"基座与本数据集的真实取值里没有与「{terms}」匹配的值，"
                f"数据集 {target.label} 无法确认该条件，未执行查询（不返回未过滤的全量结果）。"
            )
        else:
            message = (
                f"数据集 {target.label} 没有可承接「{terms}」这类语义条件的基座声明，"
                f"无法确认该条件，未执行查询（不返回未过滤的全量结果）。"
            )
        decision["message"] = message
        decision["warnings"].append(message)
    elif decision["sources"]:
        decision["status"] = "compiled"
    return conditions, decision
