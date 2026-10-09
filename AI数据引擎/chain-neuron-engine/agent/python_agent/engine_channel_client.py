# -*- coding: utf-8 -*-
"""EngineChannelClient：Agent 访问引擎的唯一出口（HTTP + 服务令牌）。"""
from __future__ import annotations

import os
import time

import httpx

from engine.core.logging import get_logger

logger = get_logger("agent.channel")

# 基座取值探测缓存（数据集 + 词）：同一轮对话里反复问同一个地点不再打引擎
_PROBE_TTL = 300.0
_PROBE_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}


class EngineChannelClient:
    def __init__(
        self,
        base_url: str | None = None,
        service_token: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (
            base_url
            or os.environ.get("ENGINE_BASE_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        self.service_token = (
            service_token or os.environ.get("ENGINE_SERVICE_TOKEN",
                                           "dev-service-token-please-rotate")
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-Service-Token": self.service_token},
            timeout=timeout,
        )

    def _post(self, path: str, payload: dict) -> dict:
        r = self._client.post(path, json=payload)
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict:
        r = self._client.get(path)
        r.raise_for_status()
        return r.json()

    # ---------------- 通道方法（与 OpenAPI 契约一一对应） ----------------

    def healthz(self) -> dict:
        return self._get("/healthz")

    def query_records(self, dataset: str, filters: dict,
                      page: int = 1, page_size: int = 20) -> dict:
        return self._post("/api/v1/engine/records/query", {
            "dataset": dataset, "filters": filters,
            "page": page, "page_size": page_size,
        })

    def query_aggregations(self, dataset: str, conditions: dict,
                           group_by: str | None = None,
                           base_filters: dict | None = None) -> dict:
        return self._post("/api/v1/engine/aggregations/query", {
            "dataset": dataset, "conditions": conditions, "group_by": group_by,
            "base_filters": base_filters or {},
        })

    def unified_query(self, payload: dict) -> dict:
        """统一查询入口；动态数据集和基座条件必须走这里。"""
        return self._post("/api/v1/engine/query", payload)

    def plan_derivation(self, payload: dict) -> dict:
        """日均/完成率/时间进度/欠进度：天数与除法全在引擎侧。"""
        return self._post("/api/v1/engine/plan-derivation", payload)

    def get_tree(self, dataset: str, max_depth: int = 4) -> dict:
        return self._get(
            f"/api/v1/engine/spiral/tree?dataset={dataset}&max_depth={max_depth}"
        )

    def start_import(self, dataset: str) -> dict:
        return self._client.post(
            "/api/v1/engine/import/test-data", data={"dataset": dataset}
        ).json()

    def get_import_task(self, task_uuid: str) -> dict:
        return self._get(f"/api/v1/engine/import/tasks/{task_uuid}")

    def grow(self, dataset: str) -> dict:
        return self._post("/api/v1/engine/grow", {"dataset": dataset})

    def list_datasources(self) -> dict:
        return self._get("/api/v1/engine/datasources")

    def list_datasets(self) -> dict:
        """数据集注册表（内置 + 界面注册），用于按 label 匹配目标数据集。"""
        return self._get("/api/v1/engine/datasets")

    def dataset_contract(self, dataset: str) -> dict:
        """读取注册数据集的动态字段/能力契约，供 Agent 解释和路由。"""
        return self._get(
            f"/api/v1/engine/datasets/{dataset}/contract"
        )

    def level_values(self, dataset: str, level: str,
                     q: str | None = None) -> dict:
        """层级取值检索（引擎真实节点值，精确→包含回退）。"""
        params = {"q": q} if q else {}
        r = self._client.get(
            f"/api/v1/engine/datasets/{dataset}/levels/{level}/values",
            params=params,
        )
        r.raise_for_status()
        return r.json()

    def semantic_probe(self, dataset: str, term: str) -> tuple[bool, bool]:
        """(是否命中, 是否配置了可承接的组织/产品特征列)。

        取值是否真实存在由引擎判定（数据集自己的列 + 已启用基座），Agent 不查词表。
        """
        text = str(term or "").strip()
        if not text:
            return False, False
        cache_key = (dataset, text)
        now = time.monotonic()
        cached = _PROBE_CACHE.get(cache_key)
        if cached and now - cached[0] < _PROBE_TTL:
            return cached[1]
        try:
            r = self._client.get(
                f"/api/v1/engine/datasets/{dataset}/semantic-probe",
                params={"term": text},
            )
            r.raise_for_status()
            payload = r.json()
            result = (bool(payload.get("matched")), bool(payload.get("configured")))
        except Exception as e:
            # 探测失败时保留原条件：宁可让引擎如实报无命中，也不静默放宽范围
            logger.warning("基座取值探测失败 dataset=%s term=%s：%s", dataset, text, e)
            return True, True
        _PROBE_CACHE[cache_key] = (now, result)
        return result

    def confirm_base_terms(
        self, datasets: list[str], base_filters: dict
    ) -> tuple[dict, list[str]]:
        """探测基座语义条件并给出说明，不静默放宽用户范围。

        提问解析阶段已经负责剔除被时间、分组、数据集名称消费的噪声。
        条件已配置但真实取值不存在时，必须继续下传原条件，由统一引擎
        返回零结果/阻断；否则删除条件会把未知地点或产品扩大成全量查询。
        """
        kept = dict(base_filters or {})
        notes: list[str] = []
        for key, value in (base_filters or {}).items():
            terms = [str(item).strip() for item in
                     (value if isinstance(value, (list, tuple)) else [value])
                     if str(item).strip()]
            if not terms:
                continue
            probed = [
                self.semantic_probe(dataset, term)
                for dataset in datasets for term in terms
            ]
            if any(matched for matched, _ in probed):
                continue
            label = f"{key}={'、'.join(terms)}"
            if not any(configured for _, configured in probed):
                notes.append(
                    f"条件“{label}”无法翻译：数据集未声明可承接组织/品类的基座特征，"
                    f"本次将由引擎阻断（请到「基座特征配置」声明名称/编码列）"
                )
            else:
                notes.append(
                    f"条件“{label}”在数据集真实取值中不存在，本次将由引擎阻断（不猜值）"
                )
        return kept, notes

    # 时间类条件键不走值解析（值本身就是数字/编码）
    _TIME_KEYS = {
        "year", "week", "season", "month", "quarter",
        "relative_period", "date_from", "date_to",
    }

    def resolve_conditions(self, dataset: str, conditions: dict) -> dict:
        """把条件值对齐到引擎里真实存在的层级值。

        唯一命中→替换为全称；多命中→传列表（引擎路由全部纳入并同值合并）；
        零命中/异常→保留原值（引擎侧还有包含匹配回退兜底）。
        候选全部来自引擎，Agent 不编造值。
        """
        out = dict(conditions)
        for key, val in (conditions or {}).items():
            if key in self._TIME_KEYS or not isinstance(val, str) or not val:
                continue
            if val.isdigit():
                continue
            try:
                res = self.level_values(dataset, key, val)
            except Exception:
                continue
            items = [it.get("value") for it in res.get("items", [])]
            items = [v for v in items if v]
            if len(items) == 1 and items[0] != val:
                out[key] = items[0]
            elif len(items) > 1:
                out[key] = items
        return out

    def test_datasource(self, config: dict) -> dict:
        return self._post("/api/v1/engine/datasources/test", {"config": config})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EngineChannelClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
