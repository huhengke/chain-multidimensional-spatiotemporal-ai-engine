"""CNTS immutable-Catalog adapter for the stable unified query contract."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import yaml
from sqlalchemy.orm import Session

from engine.core.config import ENGINE_ROOT
from engine.core.errors import BadRequestError
from engine.query.time_foundation import compile_time_conditions
from engine.query.unified import _request_filters, execute_unified_query
from engine.runtime_profiles.context import RuntimeProfileContext
from engine.runtime_profiles.models import RuntimeProfile
from engine.storage.adapters.base import StorageAdapter


TIME_KEYS = {
    "year",
    "half_year",
    "quarter",
    "season",
    "month",
    "yyyymm",
    "month_parity",
    "week",
    "weekday_type",
    "day",
    "relative_period",
    "date_from",
    "date_to",
    "calendar",
    "event",
    "anchor_date",
}
VIRTUAL_TIME_GROUPS = {
    "year",
    "half_year",
    "quarter",
    "season",
    "month",
    "month_parity",
    "week",
    "weekday_type",
    "day",
}
PRODUCT_CATEGORY_ALIASES = {
    "类别甲": "01",
    "类别乙": "02",
    "类别丁": "03",
    "类别丙": "04",
    "类别戊": "05",
}
MACHINE_OUTPUT_TERMS = {"指定类别合计", "类别甲和类别乙", "类别甲、类别乙"}
GROUP_ALIASES = {
    "big_area": "branch",
    "bigarea": "branch",
    "company": "branch",
    "组织层级一": "branch",
    "branch": "county_company",
    "county_company": "county_company",
    "组织层级二": "county_company",
    "area": "area",
    "组织层级三": "area",
    "station": "station",
    "site": "station",
    "定位单元": "station",
    "gas_code": "gascode",
    "product_code": "gascode",
    "产品": "gascode",
    "product": "category",
    "product_type": "category",
    "产品类型": "category",
}


@dataclass(slots=True)
class _CntsRuntime:
    root: Path
    catalog: Any
    query_contract: dict[str, Any]
    organization_bridge: dict[str, Any]
    topology_contract: dict[str, Any]
    assets: dict[str, Any]
    manifests: dict[str, dict[str, Any]]
    schemas: dict[str, dict[str, Any]]
    dimension_rows: dict[str, list[dict[str, Any]]]
    clients: dict[str, Any]
    display_maps: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    value_maps: dict[tuple[str, str], list[str]] = field(default_factory=dict)


class CntsAdapter(StorageAdapter):
    storage_type = "cnts"

    def __init__(self, python_root: str | Path | None = None) -> None:
        self.python_root = Path(
            python_root
            or os.environ.get("CNTS_PYTHON_ROOT")
            or (ENGINE_ROOT.parent.parent / "自研AI数据引擎" / "python")
        ).resolve()
        self._cache: dict[tuple[Any, ...], _CntsRuntime] = {}
        self._lock = threading.RLock()

    def health(self, profile: RuntimeProfile) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            runtime = self._load_runtime(
                root=profile.storage.root,
                catalog_id=profile.catalog.catalog_id,
                catalog_hash=profile.catalog.catalog_hash,
                datasets=profile.catalog.datasets,
                contract_version=profile.storage.contract_version,
                validate=True,
            )
        except Exception as exc:
            return {
                "status": "error",
                "message": f"CNTS Catalog 验收失败：{exc}",
                "fallback": "forbidden",
            }
        topology_checks: dict[str, dict[str, Any]] = {}
        query_checks: dict[str, Any] = {}
        try:
            for dataset, entry in runtime.catalog.datasets.items():
                if entry.kind != "fact":
                    continue
                payload = self._topology_payload(runtime, dataset, 12)
                summary = dict(payload.get("summary") or {})
                nodes = list(payload.get("nodes") or [])
                node_ids = {str(node.get("node_id") or "") for node in nodes}
                orphan_ids = [
                    str(node.get("node_id") or "")
                    for node in nodes
                    if node.get("parent_id")
                    and str(node.get("parent_id")) not in node_ids
                ]
                if not nodes or orphan_ids:
                    raise RuntimeError(
                        f"{dataset} 拓扑切片不完整：nodes={len(nodes)}, "
                        f"orphans={orphan_ids[:3]}"
                    )
                topology_checks[dataset] = {
                    "generation_id": entry.version_id,
                    "sampled_nodes": len(nodes),
                    "node_count": int(summary.get("node_count") or 0),
                    "temporal_layers": int(summary.get("temporal_layers") or 0),
                    "branch_nodes": int(summary.get("branch_nodes") or 0),
                    "parent_closed": True,
                }

            context = RuntimeProfileContext.from_profile(profile)
            fact_entries = [
                (dataset, entry)
                for dataset, entry in runtime.catalog.datasets.items()
                if entry.kind == "fact" and entry.time_range is not None
            ]
            end_dates = {
                dataset: date.fromisoformat(str(entry.time_range.end))
                for dataset, entry in fact_entries
            }
            latest_year = max(value.year for value in end_dates.values())
            scope_result = self.execute_query(
                None,  # CNTS reads immutable files and does not use the SQL session.
                {
                    "dataset_scope": "all_actual",
                    "period": str(latest_year),
                    "group_by": [],
                    "comparisons": [],
                    "conditions": {},
                    "filters": {},
                    "base_filters": {},
                    "detail": False,
                },
                "profile-acceptance:all-actual",
                context,
            )
            selected = list((scope_result.get("query_plan") or {}).get("datasets") or [])
            if not selected or not (scope_result.get("actual") or {}).get("available"):
                raise RuntimeError("all_actual 多数据集查询没有可用结果")
            query_checks["all_actual"] = {
                "year": latest_year,
                "datasets": selected,
                "dataset_count": int(
                    (scope_result.get("actual") or {}).get("dataset_count") or 0
                ),
            }

            daily_dataset, daily_entry = next(
                (
                    item
                    for item in fact_entries
                    if item[0] == "oil_sales_daily"
                ),
                next(
                    (
                        item
                        for item in fact_entries
                        if item[1].time_granularity == "day"
                    ),
                    (None, None),
                ),
            )
            if daily_dataset and daily_entry:
                daily_schema = runtime.schemas[daily_dataset]
                daily_groups = list(daily_schema.get("growth_dimensions") or [])[:2]
                if not daily_groups:
                    daily_groups = list(daily_schema.get("outer_dimensions") or [])[:2]
                day = end_dates[daily_dataset].isoformat()
                daily_result = self.execute_query(
                    None,
                    {
                        "datasets": [daily_dataset],
                        "period": {"start": day, "end": day},
                        "group_by": daily_groups,
                        "comparisons": [],
                        "metrics": [],
                        "conditions": {},
                        "filters": {},
                        "base_filters": {},
                        "detail": False,
                    },
                    "profile-acceptance:multi-dimension",
                    context,
                )
                daily_actual = daily_result.get("actual") or {}
                if daily_actual.get("unsupported_conditions") or daily_actual.get("precision_gap"):
                    raise RuntimeError(f"{daily_dataset} 多维聚合契约未通过")
                query_checks["multi_dimension"] = {
                    "dataset": daily_dataset,
                    "date": day,
                    "group_by": daily_groups,
                    "record_count": int(daily_actual.get("record_count") or 0),
                }

            monthly_dataset, monthly_entry = next(
                (
                    item
                    for item in fact_entries
                    if item[0] == "oil_monthly_sales"
                ),
                next(
                    (
                        item
                        for item in fact_entries
                        if item[1].time_granularity == "month"
                    ),
                    (None, None),
                ),
            )
            if monthly_dataset and monthly_entry:
                month = end_dates[monthly_dataset].strftime("%Y%m")
                comparison_result = self.execute_query(
                    None,
                    {
                        "datasets": [monthly_dataset],
                        "period": month,
                        "group_by": [],
                        "comparisons": ["yoy", "mom"],
                        "metrics": [],
                        "conditions": {},
                        "filters": {},
                        "base_filters": {},
                        "detail": False,
                    },
                    "profile-acceptance:comparisons",
                    context,
                )
                comparison_unit = (
                    ((comparison_result.get("actual") or {}).get("datasets") or {})
                    .get(monthly_dataset)
                    or {}
                )
                comparison_names = sorted(
                    (comparison_unit.get("comparisons") or {}).keys()
                )
                if comparison_names != ["mom", "yoy"]:
                    raise RuntimeError(f"{monthly_dataset} 同环比契约未通过")
                query_checks["comparisons"] = {
                    "dataset": monthly_dataset,
                    "month": month,
                    "comparisons": comparison_names,
                }
        except Exception as exc:
            return {
                "status": "error",
                "message": f"CNTS 真实拓扑或查询接口验收失败：{exc}",
                "catalog_id": runtime.catalog.catalog_id,
                "fallback": "forbidden",
            }
        return {
            "status": "ok",
            "message": "CNTS Catalog、固定 Generation、语义资产与真实拓扑接口校验通过",
            "catalog_id": runtime.catalog.catalog_id,
            "catalog_hash": runtime.catalog.catalog_hash,
            "datasets": sorted(runtime.catalog.datasets),
            "assets": sorted(runtime.catalog.assets),
            "contract_version": runtime.catalog.contract_version,
            "topology_checks": topology_checks,
            "query_checks": query_checks,
            "fallback": "forbidden",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def execute_query(
        self,
        session: Session,
        request: dict[str, Any],
        trace_id: str,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self._load_runtime(
            root=context.storage_root,
            catalog_id=context.catalog_id,
            catalog_hash=context.catalog_hash,
            datasets=context.datasets,
            contract_version=1,
            validate=False,
        )
        normalized = dict(request)
        scope_audit: dict[str, Any] | None = None
        if (
            not normalized.get("datasets")
            and str(normalized.get("dataset_scope") or "").strip() == "all_actual"
        ):
            selected, scope_audit = self._actual_dataset_scope(runtime, normalized)
            normalized["datasets"] = selected
            normalized["dataset_scope"] = "explicit_multi"

        def execute_dataset(
            query_session: Session,
            query_request: dict[str, Any],
            query_trace_id: str,
        ) -> dict[str, Any]:
            return self._execute_dataset(
                runtime,
                query_session,
                query_request,
                query_trace_id,
            )

        result = execute_unified_query(
            session,
            normalized,
            trace_id,
            dataset_executor=execute_dataset,
        )
        if scope_audit is not None:
            result.setdefault("query_plan", {})["runtime_scope_selection"] = scope_audit
        return result

    def runtime_for_context(self, context: RuntimeProfileContext) -> _CntsRuntime:
        return self._load_runtime(
            root=context.storage_root,
            catalog_id=context.catalog_id,
            catalog_hash=context.catalog_hash,
            datasets=context.datasets,
            contract_version=1,
            validate=False,
        )

    def list_datasets(
        self,
        _session: Session,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        items = [
            self.dataset_detail(runtime, dataset)
            for dataset in runtime.catalog.datasets
        ]
        experience = runtime.assets.get("experience_library") or {}
        actual_scope = (
            ((experience.get("base_semantics") or {}).get("dataset_scope") or {})
            .get("actual_sales")
            or {}
        )
        return {
            "version": runtime.catalog.contract_version,
            "loaded_at": runtime.catalog.created_at,
            "total": len(items),
            "items": items,
            "sales_scope": {
                "include_kinds": list(actual_scope.get("include_kinds") or ["generic"]),
                "exclude_terms": list(actual_scope.get("exclude_terms") or []),
            },
            **context.response_metadata(),
        }

    def dataset_detail(
        self,
        runtime: _CntsRuntime,
        dataset: str,
    ) -> dict[str, Any]:
        entry, meta = self._require_dataset(runtime, dataset)
        is_dimension = entry.kind == "dimension"
        schema = runtime.schemas.get(dataset) or {}
        dimensions = list(schema.get("common_dimensions") or []) + list(
            schema.get("outer_dimensions") or []
        )
        dimensions = list(dict.fromkeys(str(item) for item in dimensions))
        metrics = [
            {
                "name": str(item.get("name") or ""),
                "source": str(item.get("name") or ""),
                "column": str(item.get("name") or ""),
                "label": str(
                    (meta.get("metrics") or {}).get(str(item.get("name") or ""))
                    or item.get("name")
                    or ""
                ),
            }
            for item in (schema.get("metrics") or [])
        ]
        columns = []
        if is_dimension:
            rows = runtime.dimension_rows.get(dataset) or []
            names = list(rows[0]) if rows else []
            columns = [
                {"source": name, "name": name, "label": self._field_label(runtime, dataset, name)}
                for name in names
            ]
        levels = dimensions if not is_dimension else []
        topology = self._topology_summary(runtime, dataset) if not is_dimension else None
        structure = self._structure_payload(runtime, dataset, topology)
        source = {
            "kind": "cnts",
            "origin": "catalog",
            "catalog_id": runtime.catalog.catalog_id,
            "generation_id": entry.version_id,
        }
        return {
            "id": dataset,
            "dataset": dataset,
            "kind": "dimension" if is_dimension else "generic",
            "label": str(meta.get("label") or dataset),
            "aliases": list(meta.get("aliases") or []),
            "origin": "catalog",
            "source_origin": "catalog",
            "editable": False,
            "disabled": False,
            "can_unregister": False,
            "can_restore": False,
            "source": source,
            "source_file": None,
            "time": None
            if is_dimension
            else {
                "source": str(schema.get("time_field") or ""),
                "format": "yyyy-mm-dd"
                if entry.time_granularity == "day"
                else "yyyymm",
            },
            "levels": levels,
            "level_labels": {
                field: self._field_label(runtime, dataset, field) for field in levels
            },
            "level_value_key": {field: field for field in levels},
            "metrics": metrics,
            "metric_labels": {
                item["name"]: item["label"] for item in metrics if item["name"]
            },
            "columns": columns,
            "base": {
                "enabled": is_dimension,
                "features": [
                    {
                        "source": item["source"],
                        "name": item["name"],
                        "label": item["label"],
                        "semantic_type": "other",
                    }
                    for item in columns
                ],
            },
            "structure": structure,
            "catalog_id": runtime.catalog.catalog_id,
            "generation_id": entry.version_id,
            "row_count": entry.row_count,
            "time_range": entry.time_range.to_dict() if entry.time_range else None,
        }

    def dataset_contract(
        self,
        _session: Session,
        dataset: str,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        detail = self.dataset_detail(runtime, dataset)
        entry, meta = self._require_dataset(runtime, dataset)
        fields: list[dict[str, Any]] = []
        if entry.kind == "dimension":
            fields.extend(
                {
                    "source": item["source"],
                    "name": item["name"],
                    "label": item["label"],
                    "role": "dimension",
                }
                for item in detail.get("columns") or []
            )
        else:
            schema = runtime.schemas[dataset]
            time_field = str(schema.get("time_field") or "")
            fields.append(
                {
                    "source": time_field,
                    "name": time_field,
                    "label": "时间",
                    "role": "time",
                    "semantic_type": "time",
                    "format": detail["time"]["format"],
                    "canonical_time": True,
                }
            )
            common = set(schema.get("common_dimensions") or [])
            for level in detail.get("levels") or []:
                fields.append(
                    {
                        "source": level,
                        "name": level,
                        "label": detail["level_labels"].get(level, level),
                        "role": "dimension",
                        "level": level,
                        "feature_layer": "trunk" if level in common else "branch",
                    }
                )
            for metric in detail.get("metrics") or []:
                fields.append(
                    {
                        "source": metric["source"],
                        "name": metric["column"],
                        "label": metric["label"],
                        "role": "metric",
                        "metric": metric["name"],
                        "feature_layer": "branch",
                    }
                )
        experience = runtime.assets.get("experience_library") or {}
        aggregates = (
            (experience.get("base_semantics") or {}).get("business_aggregates") or {}
        )
        aggregates = {
            key: value
            for key, value in aggregates.items()
            if dataset in list((value or {}).get("datasets") or [])
        }
        return {
            "contract_version": runtime.catalog.contract_version,
            "registry_revision": runtime.catalog.catalog_hash,
            "dataset": dataset,
            "label": detail["label"],
            "aliases": list(meta.get("aliases") or []),
            "kind": detail["kind"],
            "source_origin": "catalog",
            "source": detail["source"],
            "time": detail["time"],
            "levels": [
                {"name": name, "label": detail["level_labels"].get(name, name)}
                for name in detail.get("levels") or []
            ],
            "metrics": detail.get("metrics") or [],
            "metric_labels": dict(detail.get("metric_labels") or {}),
            "order_key": meta.get("order_key"),
            "fields": fields,
            "base": detail["base"],
            "business_aggregates": aggregates,
            "capabilities": {
                "storage": "cnts",
                "immutable_generation": True,
                "multi_dimension_grouping": True,
                "feature_layers": {
                    "trunk": list((runtime.schemas.get(dataset) or {}).get("common_dimensions") or []),
                    "branch": list((runtime.schemas.get(dataset) or {}).get("outer_dimensions") or []),
                },
            },
            "structure": detail["structure"],
            "agent_usage": "可用于数据集发现、字段角色解释和查询条件校验；只读，不直接修改数据",
            **context.response_metadata(),
        }

    def level_values(
        self,
        _session: Session,
        dataset: str,
        level: str,
        q: str | None,
        limit: int,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        entry, _meta = self._require_dataset(runtime, dataset)
        if entry.kind != "fact":
            raise BadRequestError(f"维表数据集 {dataset} 没有智慧树层级")
        schema = runtime.schemas[dataset]
        dimensions = set(schema.get("common_dimensions") or []) | set(
            schema.get("outer_dimensions") or []
        )
        field = self._resolve_group(runtime, dataset, dimensions, level)
        if field is None:
            raise BadRequestError(
                f"数据集 {dataset} 没有层级：{level}",
                detail={"levels": sorted(dimensions), "requested": level},
            )
        metric = str((schema.get("metrics") or [{}])[0].get("name") or "")
        rows = self._raw_query(
            runtime,
            dataset,
            [(entry.time_range.start, entry.time_range.end)],
            {},
            [field],
            [metric],
        )
        org_maps = self._organization_maps(runtime, dataset)
        category_labels = self._category_labels(runtime)
        candidates = []
        for row in rows:
            value = str((row.get("dimensions") or {}).get(field) or "").strip()
            if not value or value == "__UNMAPPED__":
                continue
            display = (
                (org_maps.get(field) or {}).get(value)
                or (category_labels.get(value) if field == "category" else None)
                or value
            )
            candidates.append(
                {
                    "value": value,
                    "display": display,
                    "record_count": int(row.get("record_count") or 0),
                }
            )
        term = str(q or "").strip()
        matched = "all"
        if term:
            exact = [
                item
                for item in candidates
                if term.casefold() in {item["value"].casefold(), item["display"].casefold()}
            ]
            if exact:
                candidates, matched = exact, "exact"
            else:
                candidates = [
                    item
                    for item in candidates
                    if term.casefold() in item["value"].casefold()
                    or term.casefold() in item["display"].casefold()
                ]
                matched = "contains"
        candidates.sort(key=lambda item: (-item["record_count"], item["value"]))
        selected = candidates[: min(max(1, int(limit)), 100)]
        return {
            "dataset": dataset,
            "level": field,
            "level_label": self._field_label(runtime, dataset, field),
            "query": term or None,
            "matched": matched,
            "display_names": {item["value"]: item["display"] for item in selected},
            "items": [
                {"value": item["value"], "record_count": item["record_count"]}
                for item in selected
            ],
            **context.response_metadata(),
        }

    def semantic_probe(
        self,
        _session: Session,
        dataset: str,
        term: str,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        entry, _meta = self._require_dataset(runtime, dataset)
        if entry.kind != "fact":
            return {
                "dataset": dataset,
                "term": term,
                "matched": False,
                "configured": False,
                **context.response_metadata(),
            }
        schema = runtime.schemas[dataset]
        dimensions = set(schema.get("common_dimensions") or []) | set(
            schema.get("outer_dimensions") or []
        )
        bridges = (runtime.organization_bridge.get("datasets") or {}).get(dataset) or []
        organization = self._resolve_organization(runtime, dataset, term, None)
        product = self._resolve_product(runtime, dataset, term, dimensions)
        configured = bool(bridges) or bool({"category", "product_type", "gascode"} & dimensions)
        matches = []
        for semantic, resolved in (("organization", organization), ("product", product)):
            if resolved is None:
                continue
            field, values, labels = resolved
            matches.append(
                {
                    "semantic": semantic,
                    "field": field,
                    "values": values,
                    "labels": labels,
                }
            )
        return {
            "dataset": dataset,
            "term": term,
            "matched": bool(matches),
            "configured": configured,
            "matches": matches,
            **context.response_metadata(),
        }

    def foundation_features(
        self,
        _session: Session,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        config = json.loads(json.dumps(runtime.assets.get("foundation_features") or {}))
        config.update(
            {
                "read_only": True,
                "asset_id": runtime.catalog.assets["foundation_features"].asset_id,
                "source": "active_cnts_catalog",
                **context.response_metadata(),
            }
        )
        return config

    def time_rules(
        self,
        _session: Session,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        bundle = runtime.assets.get("time_rules") or {}
        files = bundle.get("files") or {}
        entry = files.get("time_derivation_rules.yaml") or {}
        yaml_content = str(entry.get("content") or "")
        parsed = yaml.safe_load(yaml_content) or {}
        rules = parsed.get("time_derivations") or []
        if not isinstance(rules, list):
            raise RuntimeError("CNTS time_rules 资产缺少 time_derivations")
        return {
            "version": int(parsed.get("version") or 1),
            "loaded_at": runtime.catalog.created_at,
            "rules": rules,
            "yaml": yaml_content,
            "read_only": True,
            "asset_id": runtime.catalog.assets["time_rules"].asset_id,
            "source": "active_cnts_catalog",
            **context.response_metadata(),
        }

    def topology_summary(
        self,
        _session: Session,
        dataset: str,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        summary = self._topology_summary(runtime, dataset)
        return {**summary, **context.response_metadata()}

    def topology_slice(
        self,
        _session: Session,
        dataset: str,
        limit: int,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        payload = self._topology_payload(runtime, dataset, limit)
        return {
            "dataset": dataset,
            "limit": limit,
            "items": payload.get("nodes") or [],
            **context.response_metadata(),
        }

    def tree(
        self,
        _session: Session,
        dataset: str,
        max_depth: int,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        runtime = self.runtime_for_context(context)
        payload = self._topology_payload(runtime, dataset, 400)
        nodes = payload.get("nodes") or []
        by_id = {
            str(node["node_id"]): {**node, "children": []}
            for node in nodes
            if int(node.get("level") or 0) <= max_depth
            or str((node.get("topology") or {}).get("role")) in {"base", "trunk"}
        }
        roots = []
        for node in by_id.values():
            parent = by_id.get(str(node.get("parent_id") or ""))
            if parent is None:
                roots.append(node)
            else:
                parent["children"].append(node)
        root = next(
            (
                node
                for node in roots
                if str((node.get("topology") or {}).get("role")) == "base"
            ),
            roots[0] if roots else {},
        )
        return {
            "dataset": dataset,
            "max_depth": max_depth,
            "tree": root,
            **context.response_metadata(),
        }

    def _topology_payload(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        limit: int,
    ) -> dict[str, Any]:
        entry, _meta = self._require_dataset(runtime, dataset)
        if entry.kind != "fact":
            raise BadRequestError(f"维表数据集 {dataset} 没有智慧树拓扑")
        return runtime.clients[dataset].topology_generation(
            entry.version_id,
            min(max(int(limit), 6), 1_000),
        )

    def _topology_summary(
        self,
        runtime: _CntsRuntime,
        dataset: str,
    ) -> dict[str, Any]:
        payload = self._topology_payload(runtime, dataset, 6)
        summary = dict(payload.get("summary") or {})
        summary["spatial_layers"] = [
            str(item.get("name") or "")
            for item in (runtime.topology_contract.get("layers") or [])
        ]
        return summary

    def _structure_payload(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        topology: dict[str, Any] | None,
    ) -> dict[str, Any]:
        entry = runtime.catalog.datasets[dataset]
        if entry.kind == "dimension":
            return {
                "feature_layer_source": "catalog",
                "feature_layers": [],
                "layer_counts": {"base": 0, "trunk": 0, "branch": 0},
                "dna": {"layer_count": 0, "length": entry.row_count, "keys": []},
                "spiral": {"layer_count": 0, "length": 0, "levels": []},
                "growth": {
                    "enabled": False,
                    "configured": False,
                    "stats_available": False,
                    "threshold": 0,
                    "node_count": 0,
                    "grown_node_count": 0,
                    "candidate_node_count": 0,
                    "grown_paths": [],
                },
                "aggregation": {"default_scope": "all", "scopes": []},
            }
        schema = runtime.schemas[dataset]
        common = list(schema.get("common_dimensions") or [])
        outer = list(schema.get("outer_dimensions") or [])
        metrics = [str(item.get("name") or "") for item in schema.get("metrics") or []]
        fields = [
            {
                "source": field,
                "name": field,
                "label": self._field_label(runtime, dataset, field),
                "layers": [layer],
                "layer_labels": ["中通主干" if layer == "trunk" else "中通分枝"],
                "role": "dimension",
                "semantic_type": "other",
                "query_uses": ["filter", "group_by"],
            }
            for layer, names in (("trunk", common), ("branch", outer))
            for field in names
        ]
        fields.extend(
            {
                "source": metric,
                "name": metric,
                "label": str(
                    ((runtime.query_contract.get("datasets") or {}).get(dataset) or {})
                    .get("metrics", {})
                    .get(metric, metric)
                ),
                "layers": ["branch"],
                "layer_labels": ["中通分枝"],
                "role": "metric",
                "semantic_type": "other",
                "query_uses": ["aggregate", "compare"],
            }
            for metric in metrics
        )
        topology = topology or {}
        growth_dimensions = list(schema.get("growth_dimensions") or [])
        return {
            "feature_layer_source": "catalog",
            "feature_layers": fields,
            "layer_counts": {
                "base": 0,
                "trunk": len(common) + 1,
                "branch": len(outer) + len(metrics),
            },
            "dna": {
                "layer_count": 2,
                "length": int(topology.get("node_count") or 0),
                "keys": [str(schema.get("time_field") or ""), *common],
            },
            "spiral": {
                "layer_count": int(topology.get("temporal_layers") or 0),
                "length": int(topology.get("node_count") or 0),
                "levels": [
                    {
                        "index": index,
                        "name": name,
                        "label": self._field_label(runtime, dataset, name),
                        "source": name,
                        "layers": ["trunk" if name in common else "branch"],
                    }
                    for index, name in enumerate(
                        [str(schema.get("time_field") or ""), *growth_dimensions]
                    )
                ],
            },
            "physical_topology": topology,
            "growth": {
                "enabled": True,
                "configured": bool(growth_dimensions),
                "stats_available": True,
                "threshold": int(schema.get("growth_threshold") or 0),
                "node_count": int(topology.get("node_count") or 0),
                "grown_node_count": int(topology.get("grown_nodes") or 0),
                "candidate_node_count": int(topology.get("branch_nodes") or 0),
                "plan": {
                    "source": "immutable_generation_schema",
                    "trunk_levels": common,
                    "branch_levels": growth_dimensions,
                    "physical_levels": growth_dimensions,
                },
                "grown_paths": [],
            },
            "aggregation": {
                "default_scope": "all",
                "scopes": [
                    {"key": "all", "label": "全部", "levels": len(growth_dimensions) + 1},
                    {"key": "trunk", "label": "主干", "levels": len(common) + 1},
                    {"key": "branch", "label": "分枝", "levels": len(growth_dimensions)},
                ],
            },
        }

    @staticmethod
    def _require_dataset(runtime: _CntsRuntime, dataset: str) -> tuple[Any, dict[str, Any]]:
        entry = runtime.catalog.datasets.get(dataset)
        meta = (runtime.query_contract.get("datasets") or {}).get(dataset)
        if entry is None or not isinstance(meta, dict):
            raise BadRequestError(
                f"当前 CNTS Catalog 没有数据集：{dataset}",
                detail={"known": sorted(runtime.catalog.datasets)},
            )
        return entry, meta

    @staticmethod
    def _field_label(runtime: _CntsRuntime, dataset: str, field: str) -> str:
        standard = {
            "year": "年",
            "month": "月",
            "day": "日",
            "category": "产品类型",
            "gascode": "产品编码",
        }
        if field in standard:
            return standard[field]
        level_labels = {
            "province": "省",
            "city": "城市",
            "branch": "组织层级一",
            "county_company": "组织层级二",
            "area": "组织层级三",
            "station": "定位单元",
            "organization": "组织层级",
        }
        for item in (runtime.organization_bridge.get("datasets") or {}).get(dataset) or []:
            if field in {str(item.get("code") or ""), str(item.get("name") or "")}:
                return level_labels.get(str(item.get("level") or ""), field)
        return field

    def _load_runtime(
        self,
        *,
        root: str | Path,
        catalog_id: str,
        catalog_hash: str,
        datasets: dict[str, str],
        contract_version: int,
        validate: bool,
    ) -> _CntsRuntime:
        root = Path(root).resolve()
        key = (
            str(root).casefold(),
            catalog_id,
            catalog_hash,
            tuple(sorted(datasets.items())),
            contract_version,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            CatalogStore, CatalogValidator, CntsClient = self._cnts_imports()
            catalog = CatalogStore(root / "catalogs").load(
                catalog_id, expected_hash=catalog_hash
            )
            if catalog.contract_version != contract_version:
                raise RuntimeError(
                    "Catalog contract_version 不匹配："
                    f"配置 {contract_version}，Catalog {catalog.contract_version}"
                )
            actual_bindings = {
                logical_id: item.version_id
                for logical_id, item in catalog.datasets.items()
            }
            if dict(datasets) != actual_bindings:
                raise RuntimeError(
                    "配置档数据集绑定与 Catalog 不一致；禁止部分绑定或跨 Catalog 混用"
                )
            if validate:
                CatalogValidator(root).validate(catalog)

            assets = {
                name: self._load_asset(root, item.relative_path, name)
                for name, item in catalog.assets.items()
            }
            query_contract = assets.get("query_contract") or {}
            contract_datasets = query_contract.get("datasets")
            if not isinstance(contract_datasets, dict):
                raise RuntimeError("query_contract 缺少固定的数据集查询契约")
            if set(contract_datasets) != set(catalog.datasets):
                raise RuntimeError("query_contract 的数据集集合与 Catalog 不一致")

            schemas: dict[str, dict[str, Any]] = {}
            manifests: dict[str, dict[str, Any]] = {}
            clients: dict[str, Any] = {}
            dimensions: dict[str, list[dict[str, Any]]] = {}
            for logical_id, entry in catalog.datasets.items():
                dataset_path = (root / entry.relative_path).resolve()
                self._assert_contained(dataset_path, root)
                if entry.kind == "dimension":
                    dimensions[logical_id] = self._load_dimension_rows(dataset_path)
                    continue
                client = CntsClient(dataset_path)
                manifest = client.verify_generation(entry.version_id)
                manifests[logical_id] = manifest
                schema = manifest.get("schema")
                if not isinstance(schema, dict):
                    raise RuntimeError(f"{logical_id} Generation 缺少 schema")
                schemas[logical_id] = schema
                clients[logical_id] = client

            runtime = _CntsRuntime(
                root=root,
                catalog=catalog,
                query_contract=query_contract,
                organization_bridge=assets.get("organization_bridge") or {},
                topology_contract=assets.get("topology_schema") or {},
                assets=assets,
                manifests=manifests,
                schemas=schemas,
                dimension_rows=dimensions,
                clients=clients,
            )
            self._cache[key] = runtime
            return runtime

    def _execute_dataset(
        self,
        runtime: _CntsRuntime,
        _session: Session,
        request: dict[str, Any],
        trace_id: str,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        dataset = str(request.get("dataset") or "").strip()
        entry = runtime.catalog.datasets.get(dataset)
        meta = (runtime.query_contract.get("datasets") or {}).get(dataset)
        if entry is None or not isinstance(meta, dict):
            raise BadRequestError(
                f"当前 CNTS Catalog 没有数据集：{dataset}",
                detail={"known": sorted(runtime.catalog.datasets)},
            )
        label = str(meta.get("label") or dataset)
        metric_labels = {
            str(key): str(value) for key, value in (meta.get("metrics") or {}).items()
        }
        query_plan: dict[str, Any] = {
            "dataset": dataset,
            "label": label,
            "kind": str(meta.get("kind") or entry.kind),
            "metric_labels": metric_labels,
            "storage": "cnts",
            "catalog_id": runtime.catalog.catalog_id,
            "generation_id": entry.version_id,
        }
        if entry.kind == "dimension":
            return self._query_dimension(
                runtime, dataset, request, trace_id, query_plan, started
            )

        schema = runtime.schemas[dataset]
        compiled = compile_time_conditions(
            conditions=dict(request.get("conditions") or {}),
            filters=_request_filters(request),
        )
        compiled_values = self._merge_compiled_values(
            compiled.get("filters") or {}, compiled.get("conditions") or {}
        )
        warnings = list(compiled.get("warnings") or [])
        segments, precision_gap = self._time_segments(
            compiled_values,
            entry.time_granularity,
            entry.time_range.start,
            entry.time_range.end,
        )
        query_plan["time_foundation"] = compiled.get("audit") or {}
        query_plan["time_segments"] = [
            {"start": start.isoformat(), "end": end.isoformat()}
            for start, end in segments
        ]
        if precision_gap:
            message = (
                f"数据集 {label} 是月粒度，无法精确回答日级日期范围；"
                "请使用月/季度/年度范围或构建日粒度 Generation。"
            )
            return self._unsupported_result(
                trace_id,
                query_plan,
                dataset,
                message,
                warnings,
                started,
                precision_gap=True,
            )

        dimensions = set(schema.get("common_dimensions") or []) | set(
            schema.get("outer_dimensions") or []
        )
        conditions = {
            key: value
            for key, value in compiled_values.items()
            if key not in TIME_KEYS and value not in (None, "", [])
        }
        base_filters = dict(request.get("base_filters") or {})
        if request.get("location"):
            base_filters.setdefault("organization", request["location"])
        if request.get("product"):
            base_filters.setdefault("product", request["product"])
        if request.get("grade"):
            base_filters.setdefault("grade", request["grade"])
        if request.get("products"):
            base_filters.setdefault("product", request["products"])

        native_filters: dict[str, list[str]] = {}
        display_conditions: dict[str, dict[str, Any]] = {}
        unresolved: list[str] = []
        for key, value in conditions.items():
            self._compile_condition(
                runtime,
                dataset,
                dimensions,
                str(key),
                value,
                native_filters,
                display_conditions,
                unresolved,
            )
        self._compile_base_filters(
            runtime,
            dataset,
            dimensions,
            base_filters,
            native_filters,
            display_conditions,
            unresolved,
        )
        if unresolved:
            message = "；".join(dict.fromkeys(unresolved))
            query_plan["compiled_conditions"] = native_filters
            return self._unsupported_result(
                trace_id,
                query_plan,
                dataset,
                message,
                warnings,
                started,
            )

        raw_group = request.get("group_by") or []
        if isinstance(raw_group, str):
            raw_group = [raw_group]
        group_by: list[str] = []
        for raw in raw_group:
            resolved = self._resolve_group(
                runtime, dataset, dimensions, str(raw).strip()
            )
            if resolved is None:
                unresolved.append(f"数据集 {label} 不支持分组维度：{raw}")
            elif resolved not in group_by:
                group_by.append(resolved)
        if unresolved:
            return self._unsupported_result(
                trace_id,
                query_plan,
                dataset,
                "；".join(dict.fromkeys(unresolved)),
                warnings,
                started,
            )

        declared_metrics = [
            str(item.get("name")) for item in (schema.get("metrics") or [])
        ]
        metrics = [str(item).strip() for item in request.get("metrics") or []]
        metrics = [item for item in metrics if item]
        if not metrics:
            metrics = declared_metrics
        unknown_metrics = [item for item in metrics if item not in declared_metrics]
        if unknown_metrics:
            return self._unsupported_result(
                trace_id,
                query_plan,
                dataset,
                f"数据集 {label} 没有指标：{'、'.join(unknown_metrics)}",
                warnings,
                started,
            )
        query_plan.update(
            {
                "filters": dict(request.get("filters") or {}),
                "base_filters": base_filters,
                "compiled_conditions": native_filters,
                "group_by": group_by,
                "metrics": metrics,
            }
        )

        rows: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
        native_routes: list[dict[str, Any]] = []
        detail_fallback = False
        for segment_start, segment_end in segments:
            payload = {
                "plan_version": 1,
                "datasets": [dataset],
                "time_range": {
                    "start": segment_start.isoformat(),
                    "end": segment_end.isoformat(),
                },
                "metrics": metrics,
                "filters": [
                    {
                        "field": field,
                        "op": "eq" if len(values) == 1 else "in",
                        "value": values[0] if len(values) == 1 else values,
                    }
                    for field, values in sorted(native_filters.items())
                ],
                "group_by": group_by,
                "comparisons": [],
                "merge_mode": "separate",
                "detail_policy": "allow_fallback",
            }
            result = runtime.clients[dataset].query_generation(entry.version_id, payload)
            self._merge_native_rows(rows, result.get("rows") or [])
            native_routes.extend(result.get("route") or [])
            detail_fallback = detail_fallback or bool(result.get("detail_fallback"))

        nodes, aggregations, record_count = self._materialize_rows(
            runtime,
            dataset,
            group_by,
            rows,
            display_conditions,
        )
        route = self._route_payload(
            dataset,
            native_filters,
            group_by,
            native_routes,
            record_count,
            detail_fallback,
        )
        base_decision = {
            "blocked": False,
            "filters": base_filters,
            "compiled_conditions": native_filters,
            "display_conditions": display_conditions,
            "source": "catalog_assets",
        }
        actual: dict[str, Any] = {
            "dataset": dataset,
            "available": record_count > 0,
            "nodes": nodes if group_by else [],
            "record_count": record_count,
            "aggregations": aggregations,
            "route": route,
            "base_decision": base_decision,
            "generation_id": entry.version_id,
        }
        order_key = str(meta.get("order_key") or "").strip()
        if record_count > 0 and order_key and order_key in dimensions:
            order_count = self._distinct_count(
                runtime,
                dataset,
                entry,
                segments,
                native_filters,
                order_key,
                metrics[0],
            )
            actual["order_key"] = order_key
            actual["order_count"] = order_count
        if request.get("detail"):
            warnings.append("CNTS 当前统一查询只返回聚合结果，不返回原始明细行")
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": actual,
            "plan": None,
            "warnings": list(dict.fromkeys(warnings)),
            "timing_ms": {
                "total": round((time.perf_counter() - started) * 1000, 3)
            },
        }

    def _query_dimension(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        request: dict[str, Any],
        trace_id: str,
        query_plan: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        rows = runtime.dimension_rows.get(dataset, [])
        conditions = {
            **dict(request.get("filters") or {}),
            **dict(request.get("conditions") or {}),
        }
        items = [
            row
            for row in rows
            if all(
                str(row.get(key) or "") in self._texts(value)
                for key, value in conditions.items()
            )
        ]
        query_plan["compiled_conditions"] = conditions
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": {
                "dataset": dataset,
                "available": bool(items),
                "items": items,
                "record_count": len(items),
                "base_decision": {"blocked": False, "source": "catalog_assets"},
            },
            "plan": None,
            "warnings": [],
            "timing_ms": {
                "total": round((time.perf_counter() - started) * 1000, 3)
            },
        }

    def _compile_condition(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        dimensions: set[str],
        key: str,
        value: Any,
        target: dict[str, list[str]],
        displays: dict[str, dict[str, Any]],
        unresolved: list[str],
    ) -> None:
        normalized = key.strip()
        if normalized in dimensions:
            self._add_filter(target, normalized, self._texts(value))
            return
        alias = GROUP_ALIASES.get(normalized.casefold())
        if alias in {"gascode", "category"} and alias in dimensions:
            values = self._texts(value)
            if alias == "category":
                values = [self._normalize_category(item) for item in values]
            self._add_filter(target, alias, values)
            return
        if alias:
            resolved = self._resolve_organization(runtime, dataset, value, alias)
            if resolved is not None:
                field, values, labels = resolved
                self._add_filter(target, field, values)
                displays[field] = {"value_labels": labels}
                return
        unresolved.append(f"数据集 {dataset} 无法映射条件字段：{key}")

    def _compile_base_filters(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        dimensions: set[str],
        base_filters: dict[str, Any],
        target: dict[str, list[str]],
        displays: dict[str, dict[str, Any]],
        unresolved: list[str],
    ) -> None:
        for key, value in base_filters.items():
            normalized = str(key).strip().casefold()
            if normalized in {"organization", "location", "org"}:
                resolved = self._resolve_organization(runtime, dataset, value, None)
                if resolved is None:
                    unresolved.append(
                        f"数据集 {dataset} 无法把组织条件“{'、'.join(self._texts(value))}”翻译为真实编码"
                    )
                    continue
                field, values, labels = resolved
                self._add_filter(target, field, values)
                displays[field] = {"value_labels": labels}
            elif normalized in {"product", "products", "category"}:
                resolved = self._resolve_product(runtime, dataset, value, dimensions)
                if resolved is None:
                    unresolved.append(
                        f"数据集 {dataset} 无法把产品条件“{'、'.join(self._texts(value))}”翻译为真实编码"
                    )
                    continue
                field, values, labels = resolved
                self._add_filter(target, field, values)
                displays[field] = {"value_labels": labels}
            elif normalized in {"grade", "gascode", "gas_code"}:
                values = self._texts(value)
                if "gascode" in dimensions and all(item.isdigit() for item in values):
                    self._add_filter(target, "gascode", values)
                else:
                    unresolved.append(
                        "具体产品牌号尚无主数据编码到事实 gascode 的可靠交叉映射，已阻断查询"
                    )
            else:
                unresolved.append(f"未注册的基座语义条件：{key}")

    def _resolve_organization(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        raw_terms: Any,
        level_hint: str | None,
    ) -> tuple[str, list[str], dict[str, str]] | None:
        bridges = (runtime.organization_bridge.get("datasets") or {}).get(dataset) or []
        if level_hint:
            bridges = [item for item in bridges if item.get("level") == level_hint]
        terms = self._texts(raw_terms)
        if not bridges or not terms:
            return None
        all_maps = self._organization_maps(runtime, dataset)
        resolutions: list[tuple[str, str, str]] = []
        bridge_rank = {
            str(item.get("code") or ""): index for index, item in enumerate(bridges)
        }
        for term in terms:
            direct: list[tuple[str, str, str, str]] = []
            normalized: list[tuple[str, str, str, str]] = []
            contains: list[tuple[str, str, str, str]] = []
            normalized_term = self._normalize_org(term)
            for bridge in bridges:
                code_field = str(bridge.get("code") or "")
                level = str(bridge.get("level") or "")
                for code, name in all_maps.get(code_field, {}).items():
                    if term.casefold() in {code.casefold(), name.casefold()}:
                        direct.append((code_field, code, name, level))
                    elif normalized_term and normalized_term == self._normalize_org(name):
                        normalized.append((code_field, code, name, level))
                    elif term.casefold() in name.casefold():
                        contains.append((code_field, code, name, level))
            chosen = direct or normalized or contains
            if not chosen:
                return None
            inferred_level = self._organization_level_hint(term)
            if inferred_level:
                same_level = [item for item in chosen if item[3] == inferred_level]
                if same_level:
                    chosen = same_level
            best_rank = min(bridge_rank.get(item[0], len(bridges)) for item in chosen)
            chosen = [
                item
                for item in chosen
                if bridge_rank.get(item[0], len(bridges)) == best_rank
            ]
            fields = {item[0] for item in chosen}
            if len(fields) != 1:
                return None
            resolutions.extend((field, code, name) for field, code, name, _ in chosen)
        fields = {item[0] for item in resolutions}
        if len(fields) != 1:
            return None
        field = resolutions[0][0]
        values = list(dict.fromkeys(item[1] for item in resolutions))
        labels = {item[1]: item[2] for item in resolutions}
        return field, values, labels

    @staticmethod
    def _organization_level_hint(value: str) -> str | None:
        text = re.sub(r"\s+", "", str(value or ""))
        if text.endswith("组织层级二"):
            return "county_company"
        if text.endswith("组织层级一"):
            return "branch"
        if text.endswith("组织层级三"):
            return "area"
        if text.endswith(("定位单元", "服务节点", "服务节点", "站")):
            return "station"
        return None

    def _organization_maps(
        self, runtime: _CntsRuntime, dataset: str
    ) -> dict[str, dict[str, str]]:
        cached = runtime.display_maps.get(dataset)
        if cached is not None:
            return cached
        entry = runtime.catalog.datasets[dataset]
        bridges = (runtime.organization_bridge.get("datasets") or {}).get(dataset) or []
        result: dict[str, dict[str, str]] = {}
        if entry.kind != "fact" or not bridges:
            runtime.display_maps[dataset] = result
            return result
        schema = runtime.schemas[dataset]
        first_metric = str((schema.get("metrics") or [{}])[0].get("name") or "")
        for bridge in bridges:
            code = str(bridge.get("code") or "")
            name = str(bridge.get("name") or "")
            if not code or not name:
                continue
            rows = self._raw_query(
                runtime,
                dataset,
                [(entry.time_range.start, entry.time_range.end)],
                {},
                [code, name],
                [first_metric],
            )
            values: dict[str, str] = {}
            for row in rows:
                dimensions = row.get("dimensions") or {}
                code_value = str(dimensions.get(code) or "").strip()
                name_value = str(dimensions.get(name) or "").strip()
                if code_value and name_value and "__UNMAPPED__" not in {code_value, name_value}:
                    values[code_value] = name_value
            result[code] = values
        runtime.display_maps[dataset] = result
        return result

    def _resolve_product(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        raw_terms: Any,
        dimensions: set[str],
    ) -> tuple[str, list[str], dict[str, str]] | None:
        terms = self._texts(raw_terms)
        if not terms:
            return None
        if "category" in dimensions:
            categories: list[str] = []
            labels: dict[str, str] = {}
            master = runtime.dimension_rows.get("oil_master") or []
            for term in terms:
                if term in MACHINE_OUTPUT_TERMS:
                    matched = ["01", "02"]
                elif term in PRODUCT_CATEGORY_ALIASES:
                    matched = [PRODUCT_CATEGORY_ALIASES[term]]
                elif term.isdigit() and len(term) <= 2:
                    matched = [term.zfill(2)]
                else:
                    type_codes = {
                        self._normalize_category(str(row.get("type_code") or ""))
                        for row in master
                        if term.casefold()
                        in " ".join(str(value or "") for value in row.values()).casefold()
                    }
                    type_codes.discard("")
                    if not type_codes:
                        return None
                    # A grade match cannot be broadened to its whole category.
                    broad_names = {
                        str(row.get("type_name") or "").strip()
                        for row in master
                        if self._normalize_category(str(row.get("type_code") or ""))
                        in type_codes
                    }
                    if term not in broad_names:
                        return None
                    matched = sorted(type_codes)
                for code in matched:
                    if code not in categories:
                        categories.append(code)
                    labels[code] = next(
                        (
                            str(row.get("type_name") or code)
                            for row in master
                            if self._normalize_category(str(row.get("type_code") or ""))
                            == code
                        ),
                        code,
                    )
            return "category", categories, labels
        if "product_type" in dimensions:
            values = self._dimension_values(runtime, dataset, "product_type")
            matched = [
                candidate
                for candidate in values
                if any(term.casefold() in candidate.casefold() for term in terms)
            ]
            if matched:
                return "product_type", matched, {value: value for value in matched}
        return None

    def _resolve_group(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        dimensions: set[str],
        raw: str,
    ) -> str | None:
        if raw in dimensions or raw in VIRTUAL_TIME_GROUPS:
            return raw
        alias = GROUP_ALIASES.get(raw.casefold())
        if alias in dimensions:
            return alias
        if alias in {"branch", "county_company", "area", "station"}:
            bridges = (runtime.organization_bridge.get("datasets") or {}).get(dataset) or []
            for item in bridges:
                if item.get("level") == alias and item.get("code") in dimensions:
                    return str(item["code"])
        return None

    def _materialize_rows(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        group_by: list[str],
        rows: dict[tuple[tuple[str, str], ...], dict[str, Any]],
        displays: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, str], int]:
        nodes: list[dict[str, Any]] = []
        totals: dict[str, Decimal] = {}
        record_count = 0
        org_maps = self._organization_maps(runtime, dataset) if group_by else {}
        category_labels = self._category_labels(runtime)
        for index, (_key, row) in enumerate(sorted(rows.items())):
            dimensions = dict(row["dimensions"])
            metrics = {
                key: format(value, "f") for key, value in row["metrics"].items()
            }
            count = int(row["record_count"])
            record_count += count
            for metric, value in row["metrics"].items():
                totals[metric] = totals.get(metric, Decimal("0")) + value
            display_dimensions: dict[str, str] = {}
            for field, value in dimensions.items():
                explicit = (displays.get(field) or {}).get("value_labels") or {}
                if value in explicit:
                    shown = str(explicit[value])
                elif field in org_maps and value in org_maps[field]:
                    shown = org_maps[field][value]
                elif field == "category" and value in category_labels:
                    shown = category_labels[value]
                else:
                    shown = value
                display_dimensions[field] = shown
            if group_by:
                identity = ":".join(f"{field}={value}" for field, value in dimensions.items())
                nodes.append(
                    {
                        "node_id": f"cnts:{dataset}:{index}:{identity}",
                        "level": len(group_by),
                        "level_name": "+".join(group_by),
                        "node_value": " / ".join(dimensions.values()),
                        "display_value": " / ".join(display_dimensions.values()),
                        "dimensions": dimensions,
                        "display_dimensions": display_dimensions,
                        "record_count": count,
                        "aggregations": metrics,
                        "grown": False,
                    }
                )
        return nodes, {key: format(value, "f") for key, value in totals.items()}, record_count

    def _distinct_count(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        entry: Any,
        segments: list[tuple[date, date]],
        filters: dict[str, list[str]],
        order_key: str,
        metric: str,
    ) -> int:
        rows = self._raw_query(
            runtime,
            dataset,
            [(start.isoformat(), end.isoformat()) for start, end in segments],
            filters,
            [order_key],
            [metric],
        )
        return len(
            {
                str((row.get("dimensions") or {}).get(order_key) or "")
                for row in rows
                if str((row.get("dimensions") or {}).get(order_key) or "").strip()
                not in {"", "__UNMAPPED__"}
            }
        )

    def _dimension_values(
        self, runtime: _CntsRuntime, dataset: str, field: str
    ) -> list[str]:
        cache_key = (dataset, field)
        if cache_key in runtime.value_maps:
            return runtime.value_maps[cache_key]
        entry = runtime.catalog.datasets[dataset]
        schema = runtime.schemas[dataset]
        metric = str((schema.get("metrics") or [{}])[0].get("name") or "")
        rows = self._raw_query(
            runtime,
            dataset,
            [(entry.time_range.start, entry.time_range.end)],
            {},
            [field],
            [metric],
        )
        values = sorted(
            {
                str((row.get("dimensions") or {}).get(field) or "")
                for row in rows
                if str((row.get("dimensions") or {}).get(field) or "").strip()
                not in {"", "__UNMAPPED__"}
            }
        )
        runtime.value_maps[cache_key] = values
        return values

    def _raw_query(
        self,
        runtime: _CntsRuntime,
        dataset: str,
        segments: Iterable[tuple[str, str]],
        filters: dict[str, list[str]],
        group_by: list[str],
        metrics: list[str],
    ) -> list[dict[str, Any]]:
        entry = runtime.catalog.datasets[dataset]
        output: list[dict[str, Any]] = []
        for start, end in segments:
            result = runtime.clients[dataset].query_generation(
                entry.version_id,
                {
                    "plan_version": 1,
                    "datasets": [dataset],
                    "time_range": {"start": start, "end": end},
                    "metrics": metrics,
                    "filters": [
                        {
                            "field": field,
                            "op": "eq" if len(values) == 1 else "in",
                            "value": values[0] if len(values) == 1 else values,
                        }
                        for field, values in sorted(filters.items())
                    ],
                    "group_by": group_by,
                    "comparisons": [],
                    "merge_mode": "separate",
                    "detail_policy": "allow_fallback",
                },
            )
            output.extend(result.get("rows") or [])
        return output

    @staticmethod
    def _merge_native_rows(
        target: dict[tuple[tuple[str, str], ...], dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> None:
        for row in rows:
            dimensions = {
                str(key): str(value)
                for key, value in (row.get("dimensions") or {}).items()
            }
            key = tuple(sorted(dimensions.items()))
            bucket = target.setdefault(
                key,
                {"dimensions": dimensions, "metrics": {}, "record_count": 0},
            )
            bucket["record_count"] += int(row.get("record_count") or 0)
            for metric, value in (row.get("metrics") or {}).items():
                bucket["metrics"][metric] = bucket["metrics"].get(
                    metric, Decimal("0")
                ) + Decimal(str(value or "0"))

    @staticmethod
    def _route_payload(
        dataset: str,
        conditions: dict[str, list[str]],
        group_by: list[str],
        routes: list[dict[str, Any]],
        record_count: int,
        detail_fallback: bool,
    ) -> dict[str, Any]:
        phase_by_operation = {
            "SEEK_TIME": "middle_inner",
            "DESCEND_COMMON": "middle_inner",
            "FILTER_COMMON_DETAIL": "middle_inner",
            "INTERSECT_OUTER": "outer_feature",
            "FILTER_OUTER": "outer_feature",
            "READ_STORE": "middle_store",
            "SCAN_DETAIL": "fact_result",
        }
        steps = []
        for index, item in enumerate(routes):
            operation = str(item.get("operation") or "CNTS")
            steps.append(
                {
                    "level": index,
                    "level_name": operation.lower(),
                    "level_label": operation,
                    "value": str(item.get("detail") or ""),
                    "status": "active" if int(item.get("matched") or 0) else "pruned",
                    "message": str(item.get("detail") or ""),
                    "phase": phase_by_operation.get(operation, "fact_result"),
                    "record_count": int(item.get("matched") or 0),
                    "elapsed_micros": int(item.get("elapsed_micros") or 0),
                }
            )
        return {
            "dataset": dataset,
            "mode": "cnts_detail_fallback" if detail_fallback else "cnts_materialized",
            "conditions": conditions,
            "group_by": group_by,
            "target_node_ids": [],
            "query_path": [
                "foundation",
                "middle_inner",
                "middle_store",
                "outer_feature",
                "fact_result",
            ],
            "inner_node_ids": [],
            "middle_store_node_ids": [],
            "outer_feature_keys": list(conditions),
            "physical_time_buckets": [],
            "dna_is_topology_not_query_level": True,
            "pruned_node_count": 0,
            "pruned_record_count": 0,
            "collapsed_messages": [],
            "steps": steps,
            "record_count": record_count,
        }

    @staticmethod
    def _unsupported_result(
        trace_id: str,
        query_plan: dict[str, Any],
        dataset: str,
        message: str,
        warnings: list[str],
        started: float,
        *,
        precision_gap: bool = False,
    ) -> dict[str, Any]:
        actual = {
            "dataset": dataset,
            "available": False,
            "nodes": [],
            "record_count": 0,
            "aggregations": {},
            "base_decision": {
                "blocked": not precision_gap,
                "message": message,
                "source": "catalog_assets",
            },
        }
        if precision_gap:
            actual["precision_gap"] = True
            actual["precision_gap_message"] = message
        else:
            actual["unsupported_conditions"] = True
            actual["unsupported_message"] = message
        query_plan["unsupported"] = {"message": message}
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": actual,
            "plan": None,
            "warnings": list(dict.fromkeys([*warnings, message])),
            "timing_ms": {
                "total": round((time.perf_counter() - started) * 1000, 3)
            },
        }

    @staticmethod
    def _time_segments(
        values: dict[str, Any],
        granularity: str,
        catalog_start: str,
        catalog_end: str,
    ) -> tuple[list[tuple[date, date]], bool]:
        explicit_start = str(values.get("date_from") or "").strip()
        explicit_end = str(values.get("date_to") or "").strip()
        segments: list[tuple[date, date]] = []
        precision_gap = False
        if explicit_start or explicit_end:
            if not explicit_start or not explicit_end:
                raise BadRequestError("日期范围必须同时提供 date_from 和 date_to")
            try:
                start = date.fromisoformat(explicit_start)
                end = date.fromisoformat(explicit_end)
            except ValueError as exc:
                raise BadRequestError("日期范围必须使用 YYYY-MM-DD") from exc
            if start > end:
                raise BadRequestError("date_from 不能晚于 date_to")
            segments.append((start, end))
            if granularity == "month":
                precision_gap = not (
                    start.day == 1 and end.day == monthrange(end.year, end.month)[1]
                )
        else:
            months = CntsAdapter._texts(values.get("month") or values.get("yyyymm"))
            years = CntsAdapter._texts(values.get("year"))
            quarters = CntsAdapter._texts(values.get("quarter"))
            seasons = CntsAdapter._texts(values.get("season"))
            if months:
                for raw in months:
                    text = raw.replace("-", "").replace("/", "")
                    if not re.fullmatch(r"\d{6}", text):
                        raise BadRequestError(f"无法解析月份：{raw}")
                    year, month = int(text[:4]), int(text[4:])
                    segments.append(
                        (date(year, month, 1), date(year, month, monthrange(year, month)[1]))
                    )
            elif quarters:
                for raw in quarters:
                    match = re.search(r"(?:(\d{4})\D*)?[Qq第]?([1-4])", raw)
                    year_values = [int(item) for item in years if item.isdigit()]
                    if match and match.group(1):
                        year_values = [int(match.group(1))]
                    if not match or not year_values:
                        raise BadRequestError(f"季度条件缺少年份：{raw}")
                    quarter = int(match.group(2))
                    start_month = (quarter - 1) * 3 + 1
                    for year in year_values:
                        end_month = start_month + 2
                        segments.append(
                            (
                                date(year, start_month, 1),
                                date(year, end_month, monthrange(year, end_month)[1]),
                            )
                        )
            elif seasons:
                year_values = [int(item) for item in years if item.isdigit()]
                if not year_values:
                    raise BadRequestError("季节条件必须同时指定年份")
                season_months = {
                    "春季": (3, 5),
                    "春": (3, 5),
                    "夏季": (6, 8),
                    "夏": (6, 8),
                    "秋季": (9, 11),
                    "秋": (9, 11),
                    "冬季": (12, 2),
                    "冬": (12, 2),
                }
                for year in year_values:
                    for season in seasons:
                        months_pair = season_months.get(season)
                        if months_pair is None:
                            raise BadRequestError(f"无法解析季节：{season}")
                        first, last = months_pair
                        if first <= last:
                            segments.append(
                                (
                                    date(year, first, 1),
                                    date(year, last, monthrange(year, last)[1]),
                                )
                            )
                        else:
                            segments.append((date(year, 12, 1), date(year, 12, 31)))
                            segments.append((date(year, 1, 1), date(year, 2, monthrange(year, 2)[1])))
            elif years:
                for raw in years:
                    if not re.fullmatch(r"\d{4}", raw):
                        raise BadRequestError(f"无法解析年份：{raw}")
                    year = int(raw)
                    segments.append((date(year, 1, 1), date(year, 12, 31)))
            else:
                today = date.today()
                segments.append(
                    (
                        date(today.year, today.month, 1),
                        date(today.year, today.month, monthrange(today.year, today.month)[1]),
                    )
                )
        bounded: list[tuple[date, date]] = []
        lower, upper = date.fromisoformat(catalog_start), date.fromisoformat(catalog_end)
        if granularity == "month":
            upper = date(upper.year, upper.month, monthrange(upper.year, upper.month)[1])
        for start, end in sorted(segments):
            clipped_start, clipped_end = max(start, lower), min(end, upper)
            if clipped_start <= clipped_end:
                bounded.append((clipped_start, clipped_end))
        if not bounded:
            return [], precision_gap
        merged: list[tuple[date, date]] = []
        for start, end in bounded:
            if merged and start <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged, precision_gap

    @staticmethod
    def _merge_compiled_values(*mappings: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for mapping in mappings:
            for key, value in mapping.items():
                if key not in result or result[key] in (None, "", []):
                    result[key] = value
                    continue
                left = CntsAdapter._texts(result[key])
                right = CntsAdapter._texts(value)
                overlap = [item for item in left if item in set(right)]
                if not overlap:
                    raise BadRequestError(f"查询条件冲突：{key}")
                result[key] = overlap[0] if len(overlap) == 1 else overlap
        return result

    @staticmethod
    def _add_filter(
        target: dict[str, list[str]], field: str, values: Iterable[str]
    ) -> None:
        incoming = [str(value).strip() for value in values if str(value).strip()]
        if not incoming:
            return
        if field not in target:
            target[field] = list(dict.fromkeys(incoming))
            return
        allowed = set(incoming)
        intersection = [value for value in target[field] if value in allowed]
        if not intersection:
            raise BadRequestError(f"查询条件冲突：{field}")
        target[field] = intersection

    @staticmethod
    def _texts(value: Any) -> list[str]:
        raw = value if isinstance(value, (list, tuple, set)) else [value]
        return list(
            dict.fromkeys(
                text
                for item in raw
                if (text := str(item or "").strip())
            )
        )

    @staticmethod
    def _normalize_category(value: str) -> str:
        text = str(value or "").strip()
        return text.zfill(2) if text.isdigit() and len(text) <= 2 else text

    @staticmethod
    def _normalize_org(value: str) -> str:
        text = re.sub(r"\s+", "", str(value or "").casefold())
        for suffix in ("组织层级二", "组织层级一", "公司", "组织层级三", "定位单元", "站"):
            if text.endswith(suffix) and len(text) > len(suffix):
                return text[: -len(suffix)]
        return text

    @staticmethod
    def _category_labels(runtime: _CntsRuntime) -> dict[str, str]:
        labels = dict(PRODUCT_CATEGORY_ALIASES)
        result = {value: key for key, value in labels.items()}
        for row in runtime.dimension_rows.get("oil_master") or []:
            code = CntsAdapter._normalize_category(str(row.get("type_code") or ""))
            name = str(row.get("type_name") or "").strip()
            if code and name:
                result[code] = name
        return result

    @staticmethod
    def _requested_scope_grain(request: dict[str, Any]) -> str:
        values: dict[str, Any] = {}
        for key in ("filters", "conditions"):
            raw = request.get(key)
            if isinstance(raw, dict):
                values.update(raw)
        period = request.get("period")
        if isinstance(period, dict):
            values.update(period)
        groups = CntsAdapter._texts(request.get("group_by"))
        if any(group in {"day", "week", "weekday_type"} for group in groups):
            return "day"
        if any(values.get(key) not in (None, "", []) for key in ("date_from", "date_to", "start", "end", "day", "week")):
            return "day"
        relative = " ".join(CntsAdapter._texts(values.get("relative_period"))).casefold()
        if any(token in relative for token in ("week", "day", "周", "日", "昨天", "今天")):
            return "day"
        return "month"

    @staticmethod
    def _actual_dataset_scope(
        runtime: _CntsRuntime, request: dict[str, Any]
    ) -> tuple[list[str], dict[str, Any]]:
        datasets = runtime.query_contract.get("datasets") or {}
        grain = CntsAdapter._requested_scope_grain(request)
        by_domain: dict[str, list[tuple[int, bool, str]]] = {}
        for dataset, meta in datasets.items():
            if not isinstance(meta, dict):
                continue
            scope = meta.get("scope") or {}
            if not isinstance(scope, dict) or scope.get("role") != "actual":
                continue
            domain = str(scope.get("business_domain") or dataset).strip()
            preferred = {str(item) for item in scope.get("preferred_for") or []}
            try:
                priority = int(scope.get("priority") or 0)
            except (TypeError, ValueError):
                priority = 0
            by_domain.setdefault(domain, []).append(
                (priority, grain in preferred, str(dataset))
            )

        selected: list[str] = []
        decisions: list[dict[str, Any]] = []
        for domain, candidates in sorted(by_domain.items()):
            candidates.sort(key=lambda item: (item[1], item[0], item[2]), reverse=True)
            priority, preferred, dataset = candidates[0]
            selected.append(dataset)
            decisions.append(
                {
                    "business_domain": domain,
                    "dataset": dataset,
                    "requested_grain": grain,
                    "preferred_match": preferred,
                    "priority": priority,
                    "excluded_overlaps": [item[2] for item in candidates[1:]],
                }
            )
        return selected, {
            "policy": "one_dataset_per_business_domain",
            "requested_grain": grain,
            "selected": selected,
            "decisions": decisions,
        }

    @staticmethod
    def _load_asset(root: Path, relative_path: str, name: str) -> dict[str, Any]:
        path = (root / relative_path).resolve()
        CntsAdapter._assert_contained(path, root)
        try:
            text = path.read_text(encoding="utf-8")
            payload = (
                yaml.safe_load(text)
                if path.suffix.casefold() in {".yaml", ".yml"}
                else json.loads(text)
            )
        except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise RuntimeError(f"无法读取 Catalog 资产 {name}：{exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Catalog 资产 {name} 必须是对象")
        return payload

    @staticmethod
    def _load_dimension_rows(root: Path) -> list[dict[str, Any]]:
        path = root / "records.jsonl"
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        item = json.loads(line)
                        if isinstance(item, dict):
                            rows.append(item)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取维表资产 {path}：{exc}") from exc
        return rows

    @staticmethod
    def _assert_contained(path: Path, root: Path) -> None:
        if path != root and root not in path.parents:
            raise RuntimeError(f"Catalog 路径越界：{path}")

    def _cnts_imports(self):
        if not self.python_root.is_dir():
            raise RuntimeError(f"CNTS Python 包目录不存在：{self.python_root}")
        root_text = str(self.python_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        try:
            from cnts_engine import CntsClient
            from cnts_engine.catalog import CatalogStore, CatalogValidator
        except ImportError as exc:
            raise RuntimeError(f"CNTS Python/native 运行时不可用：{exc}") from exc
        return CatalogStore, CatalogValidator, CntsClient
