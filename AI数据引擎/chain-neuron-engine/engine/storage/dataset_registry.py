# -*- coding: utf-8 -*-
"""数据集注册表：统一登记通用事实与维表/主数据（单一事实来源）。

公开契约只有两种数据集类型：
- ``generic`` —— 通用事实数据，参与时间/层级/指标声明驱动的统一链路；
- ``dimension`` —— 维表/主数据，不建螺旋节点，仅作为可复用的维度与基座候选。

数据集来源也只有两种：
- ``base`` —— 项目基座声明，随引擎配置交付、只读；
- ``interface`` —— 通过界面注册产生，程序读写，可增删改。

注册表只接受当前两种公开数据集类型，不再读取旧版专用类型。
"""
from __future__ import annotations

import shutil
import threading
from collections.abc import Mapping
from typing import Any
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from engine.core.config import DATA_DIR, ENGINE_CONFIG_DIR, load_yaml_config
from engine.core.errors import BadRequestError, NotFoundError
from engine.core.logging import get_logger
from engine.ingestion.cleaner import TIME_FORMATS

logger = get_logger("storage.datasets")

DATASETS_FILE = ENGINE_CONFIG_DIR / "datasets.yaml"
CUSTOM_DATASETS_FILE = DATA_DIR / "datasets_custom.yaml"
DISABLED_BASE_DATASETS_FILE = DATA_DIR / "datasets_base_disabled.yaml"
SNAPSHOT_DIR = DATA_DIR / "dataset_snapshots"

KIND_GENERIC = "generic"
KIND_DIMENSION = "dimension"
VALID_KINDS = (KIND_GENERIC, KIND_DIMENSION)
SPIRAL_KINDS = (KIND_GENERIC,)

_CUSTOM_HEADER = (
    "# 界面注册数据集注册表（由界面「数据源注册」自动维护，请勿手工编辑格式）\n"
    "# 每条记录的字段语义见 engine/config/datasets.yaml 头部说明。\n"
)
_DISABLED_BASE_HEADER = (
    "# 已注销的基座数据集运行态清单（由数据集管理自动维护，请勿手工编辑格式）\n"
    "# 基座声明仍保留在 engine/config/datasets.yaml，可通过恢复操作重新启用。\n"
)


@dataclass
class MetricSpec:
    """指标定义：节点聚合名 / 记录字段来源 / 物理列名。"""

    name: str
    source: str
    column: str
    label: str = ""


@dataclass
class ColumnSpec:
    """维表列定义：Excel 表头 → 物理列名。"""

    source: str
    name: str
    label: str = ""


@dataclass
class DatasetSpec:
    """一个数据集的完整声明。"""

    id: str
    kind: str = KIND_GENERIC
    label: str = ""
    fact_table: str = ""
    dim_table: str = ""
    primary_key: str = ""
    hooks: str = ""
    source_file: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    json_level: int = 4
    levels: tuple[str, ...] = ()
    level_labels: dict[str, str] = field(default_factory=dict)
    level_value_key: dict[str, str] = field(default_factory=dict)
    metrics: tuple[MetricSpec, ...] = ()
    fact_columns: tuple[str, ...] = ()
    fingerprint: tuple[str, ...] = ()
    columns: tuple[ColumnSpec, ...] = ()
    # ---- 通用数据集（generic）映射声明 ----
    time_source: str = ""            # 时间列（Excel 表头）
    time_format: str = "yyyymm"      # 见 cleaner.TIME_FORMATS
    derive: tuple[str, ...] = ()     # 由时间派生的字段（season/quarter 等）
    required: tuple[str, ...] = ()   # 必填字段（记录键）
    column_sources: dict[str, str] = field(default_factory=dict)  # 记录键 → Excel 列
    order_key: str = ""              # 可选记录/业务主键，用于 count(distinct)
    base: dict[str, Any] = field(default_factory=dict)  # 可选基座声明与特征映射
    capabilities: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @property
    def is_spiral(self) -> bool:
        """是否参与螺旋树物化（通用事实）。"""
        return self.kind in SPIRAL_KINDS

    @property
    def primary_column(self) -> str:
        """维表主键的物理列名（primary_key 声明为 Excel 表头时自动映射）。"""
        for col in self.columns:
            if col.source == self.primary_key:
                return col.name
        return self.primary_key


def _validate_generic(item: dict, ds_id: str) -> None:
    """校验通用数据集（generic）的映射声明。"""
    time_cfg = item.get("time")
    if time_cfg is None:
        raise BadRequestError(f"通用数据集 {ds_id} 缺少 time 声明（时间列）")
    if not isinstance(time_cfg, dict):
        raise BadRequestError(f"通用数据集 {ds_id} 的 time 必须为映射")
    if not str(time_cfg.get("source") or "").strip():
        raise BadRequestError(f"通用数据集 {ds_id} 缺少 time.source（时间列）")
    fmt = str(time_cfg.get("format") or "yyyymm")
    if fmt not in TIME_FORMATS:
        raise BadRequestError(
            f"通用数据集 {ds_id} 的 time.format 非法：{fmt}",
            detail={"valid": list(TIME_FORMATS)},
        )

    for m in item.get("metrics") or []:
        if not isinstance(m, dict):
            raise BadRequestError(f"通用数据集 {ds_id} 存在非法指标定义")
        if not str(m.get("source") or m.get("name") or "").strip():
            raise BadRequestError(f"通用数据集 {ds_id} 的指标缺少 source")
        if not str(m.get("column") or m.get("source") or m.get("name") or "").strip():
            raise BadRequestError(f"通用数据集 {ds_id} 的指标缺少 column")

    lvk = item.get("level_value_key") or {}
    derive = {str(v) for v in (item.get("derive") or [])}
    metric_sources = {
        str(m.get("source") or m.get("name"))
        for m in (item.get("metrics") or []) if isinstance(m, dict)
    }
    order_key = str(item.get("order_key") or "").strip()
    if order_key:
        declared = set(str(v) for v in (item.get("fact_columns") or []))
        if order_key not in declared:
            raise BadRequestError(
                f"通用数据集 {ds_id} 的 order_key 未出现在 fact_columns 中",
                detail={"order_key": order_key},
            )
    known = {str(v) for v in lvk.values()} | derive | metric_sources
    unknown = sorted(str(v) for v in (item.get("required") or []) if str(v) not in known)
    if unknown:
        raise BadRequestError(
            f"通用数据集 {ds_id} 的 required 含未知字段",
            detail={"unknown": unknown, "known": sorted(known)},
        )


class DatasetRegistry:
    """数据集注册表：加载/校验/热更新/落盘。"""

    def __init__(self, builtin_path: Path = DATASETS_FILE,
                 custom_path: Path = CUSTOM_DATASETS_FILE,
                 disabled_path: Path = DISABLED_BASE_DATASETS_FILE):
        self.builtin_path = builtin_path
        self.custom_path = custom_path
        self.disabled_path = disabled_path
        self.version = 0
        self.revision = 0  # 每次加载/变更自增，供视图缓存失效
        self.base_specs: dict[str, DatasetSpec] = {}
        self.interface_specs: dict[str, DatasetSpec] = {}
        self.disabled_base_ids: set[str] = set()
        self.specs: dict[str, DatasetSpec] = {}
        self.loaded_at: datetime | None = None
        self._lock = threading.RLock()
        self.load()

    # ---------------- 加载 / 热更新 ----------------

    def load(self) -> dict:
        """重新读取基座与界面注册定义（基座先加载，禁止同名覆盖）。"""
        with self._lock:
            raw = load_yaml_config(self.builtin_path)
            self._validate(raw, self.builtin_path.name, allow_empty=False)
            base = {
                spec.id: spec for spec in (self._parse(i) for i in raw.get("datasets", []))
            }
            disabled_base_ids = self._read_disabled_base_ids()

            custom_raw = load_yaml_config(self.custom_path)
            self._validate(custom_raw, self.custom_path.name, allow_empty=True)
            interface = {
                spec.id: spec
                for spec in (self._parse(i) for i in custom_raw.get("datasets", []) or [])
            }
            collide = set(base) & set(interface)
            if collide:
                raise BadRequestError(
                    f"界面注册数据集 id 与基座数据集冲突：{sorted(collide)}",
                    detail={"base": sorted(base), "interface": sorted(interface)},
                )

            self.base_specs = base
            self.interface_specs = interface
            self.disabled_base_ids = disabled_base_ids & set(base)
            active_base = {
                dataset: spec for dataset, spec in base.items()
                if dataset not in self.disabled_base_ids
            }
            self.specs = {**active_base, **interface}
            self.version = int(raw.get("version", 1))
            self.revision += 1
            self.loaded_at = datetime.now()
            logger.info(
                "数据集注册表加载 version=%d base=%d active=%d interface=%d disabled=%d",
                self.version, len(base), len(self.specs), len(interface),
                len(self.disabled_base_ids),
            )
            return self.raw()

    def reload(self) -> dict:
        return self.load()

    def raw(self, *, include_disabled: bool = True) -> dict:
        """合并后的注册表快照。

        ``include_disabled`` 仅用于恢复/迁移场景。对外主列表应使用
        ``False``，使已注销的基座数据集不再伪装成当前可用数据集。
        """
        items = []
        for spec in self.specs.values():
            item = dump_spec(spec)
            source_origin = "interface" if spec.id in self.interface_specs else "base"
            item["source_origin"] = source_origin
            # origin 保留为前端/旧 Agent 的兼容字段，但值已统一为公开语义。
            item["origin"] = source_origin
            item["editable"] = source_origin == "interface"
            item["disabled"] = False
            item["can_unregister"] = True
            items.append(item)
        if include_disabled:
            for dataset in sorted(self.disabled_base_ids):
                spec = self.base_specs[dataset]
                item = dump_spec(spec)
                item["source_origin"] = "base"
                item["origin"] = "base"
                item["editable"] = False
                item["disabled"] = True
                item["can_unregister"] = False
                item["can_restore"] = True
                items.append(item)
        return {"version": self.version, "datasets": items}

    def all_specs(self) -> dict[str, DatasetSpec]:
        """返回全部声明，包括已停用的基座数据集。"""
        return {**self.base_specs, **self.interface_specs}

    def require_any(self, dataset: str) -> DatasetSpec:
        """按声明读取数据集，允许迁移/清理阶段访问已停用基座。"""
        spec = self.all_specs().get(dataset)
        if spec is None:
            raise BadRequestError(
                f"未知数据集：{dataset}",
                detail={"known": sorted(self.all_specs())},
            )
        return spec

    # ---------------- 增删改（仅自定义层） ----------------

    def upsert(self, spec: DatasetSpec) -> DatasetSpec:
        """新增或覆盖一个自定义数据集定义，并落盘。"""
        with self._lock:
            base = self.base_specs.get(spec.id)
            if base is not None:
                raise BadRequestError(
                    f"数据集 {spec.id} 属于基座数据，禁止通过界面注册接口修改",
                    detail={"kind": base.kind, "source_origin": "base"},
                )
            payload = {"version": self.version, "datasets": [
                dump_spec(s) for s in {**self.interface_specs, spec.id: spec}.values()
            ]}
            self._validate(payload, self.custom_path.name, allow_empty=False)
            self._write_custom(payload)
            self.load()
            logger.info("自定义数据集已登记 id=%s kind=%s", spec.id, spec.kind)
            return self.require(spec.id)

    def remove(self, dataset: str) -> None:
        """删除一个自定义数据集定义。"""
        with self._lock:
            existing = self.specs.get(dataset)
            if existing is None:
                raise NotFoundError(f"数据集不存在：{dataset}")
            if dataset not in self.interface_specs:
                raise BadRequestError(
                    f"数据集 {dataset} 属于基座数据，不支持通过界面注销"
                )
            remaining = {k: v for k, v in self.interface_specs.items() if k != dataset}
            payload = {"version": self.version,
                       "datasets": [dump_spec(s) for s in remaining.values()]}
            self._write_custom(payload)
            self.load()
            logger.info("自定义数据集已移除 id=%s", dataset)

    def disable_base(self, dataset: str) -> None:
        """注销基座数据集的运行态，不修改只读基座声明。"""
        with self._lock:
            if dataset not in self.base_specs:
                raise NotFoundError(f"基座数据集不存在：{dataset}")
            if dataset in self.disabled_base_ids:
                return
            self.disabled_base_ids.add(dataset)
            self._write_disabled_base_ids()
            self.load()
            logger.info("基座数据集已注销（可恢复） id=%s", dataset)

    def restore_base(self, dataset: str) -> DatasetSpec:
        """恢复已注销的基座数据集运行态。"""
        with self._lock:
            if dataset not in self.base_specs:
                raise NotFoundError(f"基座数据集不存在：{dataset}")
            if dataset not in self.disabled_base_ids:
                return self.require(dataset)
            self.disabled_base_ids.remove(dataset)
            self._write_disabled_base_ids()
            self.load()
            logger.info("基座数据集已恢复 id=%s", dataset)
            return self.require(dataset)

    def revert(self, dataset: str, previous: DatasetSpec | None = None) -> None:
        """回滚一次注册：撤销 dataset 的声明（previous 非空则恢复为原声明）。

        用于 upsert 之后的建表/改表失败场景——避免坏声明留在 yaml 里，
        导致引擎重启建表或注销接口再次失败。
        """
        with self._lock:
            items = {k: v for k, v in self.interface_specs.items() if k != dataset}
            if previous is not None:
                items[dataset] = previous
            payload = {"version": self.version,
                       "datasets": [dump_spec(s) for s in items.values()]}
            self._write_custom(payload)
            self.load()
            logger.warning("自定义数据集已回滚 id=%s 恢复原声明=%s",
                           dataset, previous is not None)

    def snapshot(self) -> Path | None:
        """自定义注册表版本快照（供回滚）。"""
        if not self.custom_path.exists():
            return None
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target = SNAPSHOT_DIR / f"datasets_custom_r{self.revision}.yaml"
        shutil.copy2(self.custom_path, target)
        return target

    def _write_custom(self, raw: dict) -> None:
        self.custom_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.custom_path.with_suffix(".yaml.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(_CUSTOM_HEADER)
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False,
                           default_flow_style=False)
        tmp.replace(self.custom_path)

    def _read_disabled_base_ids(self) -> set[str]:
        if not self.disabled_path.exists():
            return set()
        raw = load_yaml_config(self.disabled_path)
        items = raw.get("datasets", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            raise BadRequestError(f"{self.disabled_path.name} 的 datasets 必须为列表")
        return {str(item).strip() for item in items if str(item).strip()}

    def _write_disabled_base_ids(self) -> None:
        self.disabled_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.disabled_path.with_suffix(".yaml.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(_DISABLED_BASE_HEADER)
            yaml.safe_dump({"version": 1, "datasets": sorted(self.disabled_base_ids)},
                           f, allow_unicode=True, sort_keys=False,
                           default_flow_style=False)
        tmp.replace(self.disabled_path)

    # ---------------- 解析 / 校验 ----------------

    @staticmethod
    def _parse(item: dict) -> DatasetSpec:
        metrics = tuple(
            MetricSpec(
                name=str(m["name"]),
                source=str(m.get("source") or m["name"]),
                column=str(m.get("column") or m.get("source") or m["name"]),
                label=str(m.get("label") or ""),
            )
            for m in (item.get("metrics") or [])
        )
        columns = tuple(
            ColumnSpec(
                source=str(c["source"]),
                name=str(c.get("name") or c["source"]),
                label=str(c.get("label") or ""),
            )
            for c in (item.get("columns") or [])
        )
        ds_id = str(item.get("id") or "").strip()
        time_cfg = item.get("time") or {}
        if not isinstance(time_cfg, dict):
            time_cfg = {}
        return DatasetSpec(
            id=ds_id,
            kind=str(item.get("kind") or KIND_GENERIC).strip(),
            label=str(item.get("label") or ds_id),
            fact_table=str(item.get("fact_table") or ""),
            dim_table=str(item.get("dim_table") or ""),
            primary_key=str(item.get("primary_key") or ""),
            hooks=str(item.get("hooks") or ""),
            source_file=str(item.get("source_file") or ""),
            source={str(k): v for k, v in (item.get("source") or {}).items()},
            aliases=tuple(str(v) for v in (item.get("aliases") or []) if str(v).strip()),
            json_level=int(item.get("json_level", 4)),
            levels=tuple(str(v) for v in (item.get("levels") or [])),
            level_labels={str(k): str(v)
                          for k, v in (item.get("level_labels") or {}).items()},
            level_value_key={str(k): str(v)
                             for k, v in (item.get("level_value_key") or {}).items()},
            metrics=metrics,
            fact_columns=tuple(str(v) for v in (item.get("fact_columns") or [])),
            fingerprint=tuple(str(v) for v in (item.get("fingerprint") or [])),
            columns=columns,
            time_source=str(time_cfg.get("source") or ""),
            time_format=str(time_cfg.get("format") or "yyyymm"),
            derive=tuple(str(v) for v in (item.get("derive") or [])),
            required=tuple(str(v) for v in (item.get("required") or [])),
            column_sources={str(k): str(v)
                            for k, v in (item.get("column_sources") or {}).items()},
            order_key=str(item.get("order_key") or ""),
            base={str(k): v for k, v in (item.get("base") or {}).items()},
            capabilities={str(k): v for k, v in (item.get("capabilities") or {}).items()},
            enabled=bool(item.get("enabled", True)),
        )

    @staticmethod
    def _validate(raw: dict, source: str, allow_empty: bool) -> None:
        items = raw.get("datasets")
        if items is None:
            if allow_empty:
                return
            raise BadRequestError(f"{source} 缺少 datasets 列表")
        if not isinstance(items, list):
            raise BadRequestError(f"{source} 的 datasets 必须为列表")
        if not items and not allow_empty:
            raise BadRequestError(f"{source} 的 datasets 不能为空")

        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise BadRequestError(f"{source} 存在非法条目（应为映射）")
            ds_id = str(item.get("id") or "").strip()
            if not ds_id:
                raise BadRequestError(f"{source} 存在缺少 id 的数据集定义")
            if ds_id in seen:
                raise BadRequestError(f"数据集 id 重复：{ds_id}")
            seen.add(ds_id)

            kind = str(item.get("kind") or KIND_GENERIC).strip()
            if kind not in VALID_KINDS:
                raise BadRequestError(
                    f"数据集 {ds_id} 的 kind 非法：{kind}",
                    detail={"valid": list(VALID_KINDS)},
                )
            base = item.get("base")
            if base is not None:
                if not isinstance(base, dict):
                    raise BadRequestError(f"数据集 {ds_id} 的 base 必须为映射")
                if bool(base.get("enabled")):
                    features = base.get("features")
                    if not isinstance(features, list) or not features:
                        raise BadRequestError(f"数据集 {ds_id} 启用基座后必须声明 features")
                    seen_base_sources: set[str] = set()
                    for feature in features:
                        if not isinstance(feature, dict) or not feature.get("source"):
                            raise BadRequestError(f"数据集 {ds_id} 存在非法基座特征")
                        source_name = str(feature["source"])
                        if source_name in seen_base_sources:
                            raise BadRequestError(f"数据集 {ds_id} 的基座特征重复：{source_name}")
                        seen_base_sources.add(source_name)
                        semantic_type = str(feature.get("semantic_type") or "other")
                        if semantic_type not in {
                            "product_code", "product_name", "alias", "category",
                            "org_code", "org_name", "time", "other",
                        }:
                            raise BadRequestError(
                                f"数据集 {ds_id} 的基座特征 semantic_type 非法：{semantic_type}"
                            )
            capabilities = item.get("capabilities") or {}
            feature_layers = capabilities.get("feature_layers") if isinstance(capabilities, dict) else None
            if feature_layers is not None:
                if not isinstance(feature_layers, dict):
                    raise BadRequestError(f"数据集 {ds_id} 的 capabilities.feature_layers 必须为映射")
                valid_layers = {"base", "trunk", "branch"}
                all_sources = {
                    str(c.get("source")) for c in (item.get("columns") or []) if isinstance(c, dict)
                }
                all_sources.update(
                    str(value) for value in (item.get("column_sources") or {}).values()
                )
                if isinstance(item.get("time"), dict) and item["time"].get("source"):
                    all_sources.add(str(item["time"]["source"]))
                planned_by_layer: dict[str, set[str]] = {
                    layer: set() for layer in valid_layers
                }
                for layer in valid_layers:
                    entries = feature_layers.get(layer) or []
                    if not isinstance(entries, list):
                        raise BadRequestError(
                            f"数据集 {ds_id} 的 feature_layers.{layer} 必须为列表"
                        )
                    for entry in entries:
                        if not isinstance(entry, dict) or not entry.get("source"):
                            raise BadRequestError(f"数据集 {ds_id} 存在非法特征计划项")
                        source = str(entry["source"])
                        if source not in all_sources:
                            raise BadRequestError(
                                f"数据集 {ds_id} 的特征计划字段不存在：{source}"
                            )
                        if source in planned_by_layer[layer]:
                            raise BadRequestError(
                                f"数据集 {ds_id} 的 feature_layers.{layer} 字段重复：{source}"
                            )
                        planned_by_layer[layer].add(source)
                planned = set().union(*(planned_by_layer[layer] for layer in valid_layers))
                dna_keys = feature_layers.get("dna_keys") or []
                if not isinstance(dna_keys, list) or any(str(value) not in planned for value in dna_keys):
                    raise BadRequestError(
                        f"数据集 {ds_id} 的 feature_layers.dna_keys 必须来自已确认特征"
                    )
            if kind in SPIRAL_KINDS:
                levels = item.get("levels")
                if not isinstance(levels, list) or not levels:
                    raise BadRequestError(f"数据集 {ds_id} 缺少 levels")
                if not item.get("fact_table"):
                    raise BadRequestError(f"数据集 {ds_id} 缺少 fact_table")
                metrics = item.get("metrics") or []
                if not metrics:
                    raise BadRequestError(f"数据集 {ds_id} 缺少 metrics")
                for m in metrics:
                    if not isinstance(m, dict) or not m.get("name"):
                        raise BadRequestError(f"数据集 {ds_id} 存在缺少 name 的指标")
                lvk = item.get("level_value_key") or {}
                missing = [lv for lv in levels if lv not in lvk]
                if missing:
                    raise BadRequestError(
                        f"数据集 {ds_id} 的层级缺少 level_value_key 映射",
                        detail={"missing": missing},
                    )
                if kind == KIND_GENERIC:
                    _validate_generic(item, ds_id)
            else:
                if not item.get("dim_table"):
                    raise BadRequestError(f"维表数据集 {ds_id} 缺少 dim_table")
                columns = item.get("columns") or []
                if not columns:
                    raise BadRequestError(f"维表数据集 {ds_id} 缺少 columns")
                for c in columns:
                    if not isinstance(c, dict) or not c.get("source"):
                        raise BadRequestError(
                            f"维表数据集 {ds_id} 存在缺少 source 的列定义"
                        )

    # ---------------- 查询 ----------------

    def get(self, dataset: str) -> DatasetSpec | None:
        return self.specs.get(dataset)

    def require(self, dataset: str) -> DatasetSpec:
        spec = self.specs.get(dataset)
        if spec is None:
            raise BadRequestError(f"未知数据集：{dataset}", detail={"known": self.ids()})
        return spec

    def exists(self, dataset: str) -> bool:
        return dataset in self.specs

    def ids(self) -> list[str]:
        return list(self.specs)

    def spiral_ids(self) -> list[str]:
        return [d for d, s in self.specs.items() if s.is_spiral]

    def specs_of_kind(self, kind: str) -> list[DatasetSpec]:
        return [s for s in self.specs.values() if s.kind == kind]

    def levels_of(self, dataset: str) -> list[str]:
        return list(self.require(dataset).levels)

    def level_labels_of(self, dataset: str) -> dict[str, str]:
        return dict(self.require(dataset).level_labels)

    def level_value_key_of(self, dataset: str) -> dict[str, str]:
        return dict(self.require(dataset).level_value_key)

    def metric_names_of(self, dataset: str) -> tuple[str, ...]:
        return tuple(m.name for m in self.require(dataset).metrics)

    def metric_sources_of(self, dataset: str) -> dict[str, str]:
        return {m.name: m.source for m in self.require(dataset).metrics}

    def metric_columns_of(self, dataset: str) -> dict[str, str]:
        return {m.name: m.column for m in self.require(dataset).metrics}

    def metric_labels_of(self, dataset: str) -> dict[str, str]:
        return {m.name: (m.label or m.name) for m in self.require(dataset).metrics}

    def fact_columns_of(self, dataset: str) -> tuple[str, ...]:
        return tuple(self.require(dataset).fact_columns)

    def fingerprint_of(self, dataset: str) -> tuple[str, ...]:
        return tuple(self.require(dataset).fingerprint)

    def merged_level_labels(self) -> dict[str, str]:
        """全部数据集的层级中文名合并（供全局展示字典）。"""
        merged: dict[str, str] = {}
        for spec in self.specs.values():
            merged.update(spec.level_labels)
        return merged


def dump_spec(spec: DatasetSpec) -> dict:
    """DatasetSpec → YAML 友好字典（空字段不落盘）。"""
    out: dict = {"id": spec.id, "kind": spec.kind, "label": spec.label}
    if spec.source_file:
        out["source_file"] = spec.source_file
    if spec.source:
        out["source"] = dict(spec.source)
    if spec.aliases:
        out["aliases"] = list(spec.aliases)
    if not spec.enabled:
        out["enabled"] = False

    if spec.capabilities:
        out["capabilities"] = dict(spec.capabilities)
    if spec.base:
        out["base"] = dict(spec.base)

    if spec.kind == KIND_DIMENSION:
        out["dim_table"] = spec.dim_table
        if spec.primary_key:
            out["primary_key"] = spec.primary_key
        out["columns"] = [
            {"source": c.source, "name": c.name, "label": c.label}
            for c in spec.columns
        ]
        return out

    out["fact_table"] = spec.fact_table
    if spec.hooks:
        out["hooks"] = spec.hooks
    out["json_level"] = spec.json_level
    out["levels"] = list(spec.levels)
    out["level_labels"] = dict(spec.level_labels)
    out["level_value_key"] = dict(spec.level_value_key)
    out["metrics"] = [
        {"name": m.name, "source": m.source, "column": m.column, "label": m.label}
        for m in spec.metrics
    ]
    out["fact_columns"] = list(spec.fact_columns)
    if spec.fingerprint:
        out["fingerprint"] = list(spec.fingerprint)
    if spec.kind == KIND_GENERIC:
        out["time"] = {"source": spec.time_source, "format": spec.time_format}
        if spec.derive:
            out["derive"] = list(spec.derive)
        if spec.required:
            out["required"] = list(spec.required)
        if spec.column_sources:
            out["column_sources"] = dict(spec.column_sources)
        if spec.order_key:
            out["order_key"] = spec.order_key
    return out


def parse_spec(item: dict) -> DatasetSpec:
    """由声明字典构造 DatasetSpec（供 API 层解析请求体 / 落盘前构造）。"""
    return DatasetRegistry._parse(item)


class _RegistryMapping(Mapping):
    """委托注册表的只读 Mapping 视图。

    调用方仍可像字典一样使用（`X[ds]` / `ds in X` / `X.items()`），
    但内容始终来自注册表当前快照，新增数据集无需改动调用点。
    """

    name = "registry_view"

    def _build(self) -> dict:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def __getitem__(self, key):
        try:
            return self._build()[key]
        except KeyError:
            raise KeyError(key) from None

    def __iter__(self):
        return iter(self._build())

    def __len__(self) -> int:
        return len(self._build())

    def __contains__(self, key) -> bool:
        return key in self._build()

    def get(self, key, default=None):
        return self._build().get(key, default)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<{self.name} {self._build()!r}>"


class LevelsByDataset(_RegistryMapping):
    """{dataset: [层级...]}（仅参与螺旋树的数据集）。"""

    name = "LEVELS_BY_DATASET"

    def _build(self) -> dict:
        return {ds: list(s.levels) for ds, s in registry.specs.items() if s.is_spiral}


class MetricsByDataset(_RegistryMapping):
    """{dataset: (指标名...)}。"""

    name = "METRICS_BY_DATASET"

    def _build(self) -> dict:
        return {ds: tuple(m.name for m in s.metrics)
                for ds, s in registry.specs.items() if s.is_spiral}


class LevelLabels(_RegistryMapping):
    """{层级名: 中文显示名}（全数据集合并）。"""

    name = "LEVEL_LABELS"

    def _build(self) -> dict:
        merged: dict[str, str] = {"root": "中通入口"}
        for spec in registry.specs.values():
            merged.update(spec.level_labels)
        return merged


class MetricSources(_RegistryMapping):
    """{指标名: 记录字段来源}（全数据集合并）。"""

    name = "METRIC_SOURCES"

    def _build(self) -> dict:
        merged: dict[str, str] = {}
        for spec in registry.specs.values():
            for m in spec.metrics:
                merged[m.name] = m.source
        return merged


# 模块级单例：全进程共享一份注册表
registry = DatasetRegistry()
