# -*- coding: utf-8 -*-
"""可审计特征注册：产品交叉映射、数据画像和 FeatureProposal。

本模块只从已导入的事实表和主数据生成候选/映射；不调用模型，也不直接
修改事实数据。模型或 Agent 只能消费画像与提案，再经审批发布。
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.storage.dataset_registry import registry
from engine.storage.dynamic_table import get_dynamic_dim_table
from engine.storage.fact_tables import get_fact_source
from engine.storage.models import FeatureProposal, ProductCrosswalk

logger = get_logger("features.registry")

_GRADE_RE = re.compile(r"(?<!\d)(92|95|98|0)\s*(?:号|#)")
_CATEGORY_BY_WORD = (("类别甲", "01"), ("类别乙", "02"), ("类别丙", "03"))


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _grade_from_text(*values: Any) -> str | None:
    text = " ".join(_clean(value) for value in values if _clean(value))
    if "油" not in text:
        return None
    match = _GRADE_RE.search(text)
    return match.group(1) if match else None


def _category_from_text(*values: Any) -> str | None:
    text = " ".join(_clean(value) for value in values if _clean(value))
    for word, category in _CATEGORY_BY_WORD:
        if word in text:
            return category
    return None


def _aliases(name: str, grade: str | None) -> list[str]:
    values = [name] if name else []
    if grade:
        values.extend([grade, f"{grade}号", f"{grade}#", f"{grade}号产品"])
    return list(dict.fromkeys(value for value in values if value))


def _oil_master(session: Session) -> dict[str, dict[str, Any]]:
    """从产品基础维表读取编码→名称/别名证据；维表未导入时返回空。"""
    if not registry.exists("oil_master"):
        return {}
    try:
        table = get_dynamic_dim_table(registry.require("oil_master"))
        required = ("material_code", "material_name")
        if any(name not in table.c for name in required):
            return {}
        selected = [table.c.material_code, table.c.material_name]
        for name in ("material_alias", "type_code", "type_name", "material_class_name", "extend1", "extend2"):
            if name in table.c:
                selected.append(table.c[name])
        rows = session.execute(select(*selected)).mappings().all()
    except OperationalError:
        return {}

    master: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = _clean(row.get("material_code"))
        if not code:
            continue
        name = _clean(row.get("material_name"))
        grade = _grade_from_text(*row.values())
        category = _category_from_text(*row.values())
        master[code] = {
            "name": name,
            "grade": grade,
            "category": category,
            "aliases": _aliases(name, grade) + [
                _clean(row.get("material_alias")),
                _clean(row.get("extend1")),
                _clean(row.get("extend2")),
            ],
            "evidence": {"source": "oil_master", "values": dict(row)},
        }
    return master


def _fact_codes(session: Session, dataset: str) -> list[tuple[str, str | None]]:
    """从注册表声明的通用事实表读取产品编码与类别。"""
    spec = registry.require(dataset)
    source = get_fact_source(dataset)
    product_level = next(
        (level for level in ("gascode", "gas_code", "product_code", "product")
         if level in spec.level_value_key),
        None,
    )
    if not product_level:
        return []
    product_column = source.column(spec.level_value_key.get(product_level, product_level))
    if product_column is None:
        return []
    category_level = next(
        (level for level in ("category", "type") if level in spec.level_value_key),
        None,
    )
    category_column = (
        source.column(spec.level_value_key.get(category_level, category_level))
        if category_level else None
    )
    selected = [product_column]
    if category_column is not None:
        selected.append(category_column)
    rows = session.execute(select(*selected).distinct()).all()
    if category_column is None:
        return [(_clean(row[0]), None) for row in rows if _clean(row[0])]
    return [
        (_clean(row[0]), _clean(row[1]) or None)
        for row in rows
        if _clean(row[0])
    ]


def sync_product_crosswalk(session: Session) -> dict[str, int]:
    """从主数据与已登记通用事实表重建 dataset 级产品交叉映射。

    主数据命中且可识别类别代码时自动标为 approved；其余记录留在
    pending_review，避免未知编码被静默扩展到整个产品大类。
    """
    master = _oil_master(session)
    result: dict[str, int] = {}
    for dataset in registry.spiral_ids():
        count = 0
        for source_code, fact_category in _fact_codes(session, dataset):
            item = master.get(source_code, {})
            name = _clean(item.get("name"))
            grade = item.get("grade")
            category = fact_category or item.get("category")
            aliases = list(dict.fromkeys(
                [value for value in item.get("aliases", []) if value] + _aliases(name, grade)
            ))
            evidence = item.get("evidence") or {
                "source": "fact_distinct", "dataset": dataset, "source_code": source_code,
            }
            status = "approved" if item and grade else "pending_review"
            stmt = sqlite_insert(ProductCrosswalk).values(
                dataset=dataset,
                source_code=source_code,
                category=category,
                grade=grade,
                canonical_name=name or None,
                aliases=aliases,
                evidence=evidence,
                confidence=Decimal("1.0") if status == "approved" else Decimal("0.4"),
                status=status,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["dataset", "source_code"],
                set_={
                    "category": stmt.excluded.category,
                    "grade": stmt.excluded.grade,
                    "canonical_name": stmt.excluded.canonical_name,
                    "aliases": stmt.excluded.aliases,
                    "evidence": stmt.excluded.evidence,
                    "confidence": stmt.excluded.confidence,
                    "status": stmt.excluded.status,
                    "updated_at": datetime.utcnow(),
                },
            )
            session.execute(stmt)
            count += 1
        result[dataset] = count
    logger.info("产品交叉映射已同步 %s", result)
    return result


def product_codes(
    session: Session,
    dataset: str,
    *,
    category: str | None = None,
    grade: str | None = None,
) -> list[str]:
    conditions = [ProductCrosswalk.dataset == dataset, ProductCrosswalk.status == "approved"]
    if category:
        conditions.append(ProductCrosswalk.category == category)
    if grade:
        conditions.append(ProductCrosswalk.grade == grade)
    try:
        rows = session.execute(
            select(ProductCrosswalk.source_code).where(*conditions).order_by(ProductCrosswalk.source_code)
        ).all()
    except OperationalError:
        # 兼容尚未执行 init_db 的旧测试库/旧部署；调用方会走安全的类别回退。
        return []
    return [str(value) for (value,) in rows]


def product_mapping_summary(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(ProductCrosswalk.dataset, ProductCrosswalk.status, func.count())
        .group_by(ProductCrosswalk.dataset, ProductCrosswalk.status)
    ).all()
    totals: dict[str, dict[str, int]] = {}
    for dataset, status, count in rows:
        totals.setdefault(str(dataset), {})[str(status)] = int(count)
    return {"datasets": totals}


def profile_dataset(session: Session, dataset: str) -> dict[str, Any]:
    """返回不含敏感明细的结构化数据画像，供 Agent/模型生成建议。"""
    spec = registry.require_any(dataset)
    result: dict[str, Any] = {
        "dataset": dataset,
        "label": spec.label,
        "kind": spec.kind,
        "capabilities": spec.capabilities,
    }
    if not spec.is_spiral:
        try:
            source = get_dynamic_dim_table(spec)
            result["row_count"] = int(session.execute(select(func.count()).select_from(source)).scalar_one())
        except OperationalError:
            result["row_count"] = 0
        return result

    source = get_fact_source(dataset)
    result["row_count"] = source.count(session)
    yyyymm = source.column("yyyymm")
    if yyyymm is not None:
        low, high = session.execute(select(func.min(yyyymm), func.max(yyyymm))).one()
        result["period_range"] = {"min": _clean(low) or None, "max": _clean(high) or None}
    result["metrics"] = [
        {"name": metric.name, "label": metric.label, "column": metric.column}
        for metric in spec.metrics
    ]
    result["levels"] = [
        {"name": level, "label": spec.level_labels.get(level, level)}
        for level in spec.levels
    ]
    return result


def create_feature_proposals(session: Session, dataset: str | None = None) -> list[dict[str, Any]]:
    """把未审批的映射缺口转换为可审计 FeatureProposal。"""
    conditions = [ProductCrosswalk.status == "pending_review"]
    if dataset:
        conditions.append(ProductCrosswalk.dataset == dataset)
    rows = session.execute(select(ProductCrosswalk).where(*conditions)).scalars().all()
    created: list[dict[str, Any]] = []
    for row in rows:
        payload = {
            "dataset": row.dataset,
            "source_code": row.source_code,
            "category": row.category,
            "grade": row.grade,
            "canonical_name": row.canonical_name,
            "aliases": row.aliases or [],
        }
        exists = session.execute(
            select(FeatureProposal.proposal_id).where(
                FeatureProposal.feature_type == "product_crosswalk",
                FeatureProposal.dataset == row.dataset,
                FeatureProposal.status == "pending_review",
                FeatureProposal.payload["source_code"].as_string() == row.source_code,
            )
        ).first()
        if exists:
            continue
        proposal = FeatureProposal(
            proposal_id=str(uuid4()),
            feature_type="product_crosswalk",
            dataset=row.dataset,
            status="pending_review",
            payload=payload,
            evidence=row.evidence or {},
            confidence=row.confidence or Decimal("0"),
        )
        session.add(proposal)
        created.append({"proposal_id": proposal.proposal_id, **payload, "status": proposal.status})
    return created


def list_proposals(session: Session, dataset: str | None = None) -> list[dict[str, Any]]:
    stmt = select(FeatureProposal).order_by(FeatureProposal.created_at.desc())
    if dataset:
        stmt = stmt.where(FeatureProposal.dataset == dataset)
    return [
        {
            "proposal_id": row.proposal_id,
            "feature_type": row.feature_type,
            "dataset": row.dataset,
            "status": row.status,
            "payload": row.payload,
            "evidence": row.evidence,
            "confidence": format(row.confidence or Decimal("0"), "f"),
            "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        }
        for row in session.execute(stmt).scalars().all()
    ]
