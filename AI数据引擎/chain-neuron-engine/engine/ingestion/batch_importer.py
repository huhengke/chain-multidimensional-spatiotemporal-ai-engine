# -*- coding: utf-8 -*-
"""BatchImporter：Excel 批量导入主编排。

能力：
- 导入任务状态机：pending → running → success / partial / failed；
- 实时计数：total/processed/succeeded/failed/skipped（可按任务 id 查询）；
- 分批落库（事实表 bulk + 节点 deltas 增量合并），内存平稳；
- 导入结束：维表 upsert、JSON 物化、聚合一致性校验。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.core.config import get_settings
from engine.core.logging import get_logger
from engine.core.params import get_param
from engine.features.registry import sync_product_crosswalk, create_feature_proposals
from engine.features.growth_plan import physical_levels_for_dataset
from engine.ingestion.cleaner import CleanError
from engine.ingestion.data_mapper import DataMapper
from engine.ingestion.connectors.base import create_connector
from engine.ingestion.datasource_config import get_datasource_config
from engine.ingestion.excel_reader import ExcelReader, probe_file
from engine.ingestion.generic_mapper import map_dim_row
from engine.ingestion.models import (
    MODE_APPEND,
    MODE_DEDUPE,
    MODE_REPLACE,
    VALID_MODES,
    ST_FAILED,
    ST_PARTIAL,
    ST_PENDING,
    ST_RUNNING,
    ST_SUCCESS,
    ImportTask,
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_FINALIZING,
    PHASE_MATERIALIZING,
    PHASE_PREPARING,
    PHASE_READING,
    PHASE_WRITING,
)
from engine.storage.dataset_registry import KIND_DIMENSION, registry
from engine.storage.dimensions import make_node_id
from engine.storage.fact_tables import get_dim_source, get_fact_source
from engine.storage.models import SpiralNodeORM
from engine.storage.spiral_json import purge_dataset_json
from engine.storage.spiral_store import METRICS_BY_DATASET, NodeDelta, SpiralStore

logger = get_logger("ingestion.importer")

ERROR_SAMPLE_LIMIT = 100

# 节点 metric → 记录字段名、层级 → 取值键、事实表列：均由注册表声明提供


class BatchImporter:
    def __init__(self, session: Session, mapper: DataMapper | None = None):
        self.s = session
        self.mapper = mapper or DataMapper()
        self.store = SpiralStore(session)
        self.settings = get_settings()

    # ---------------- 启动导入任务 ----------------

    def create_task(self, dataset: str, file_path: str | Path | None,
                    mode: str = MODE_DEDUPE, *,
                    source_kind: str = "excel",
                    source_ref: dict | None = None) -> ImportTask:
        if mode not in VALID_MODES:
            raise ValueError(f"非法导入模式：{mode}")
        path = Path(file_path) if file_path is not None else None
        ref = dict(source_ref or {})
        if source_kind == "database" and not ref.get("table"):
            raise ValueError("数据库导入任务缺少 table")
        task = ImportTask(
            task_uuid=str(uuid.uuid4()),
            dataset=dataset,
            file_name=path.name if path else str(ref.get("table") or "database"),
            file_path=str(path) if path else "",
            source_kind=source_kind,
            source_ref=ref,
            status=ST_PENDING,
            mode=mode,
        )
        self.s.add(task)
        self.s.commit()
        logger.info("导入任务创建 uuid=%s dataset=%s mode=%s file=%s",
                    task.task_uuid, dataset, mode, task.file_name)
        return task

    def run_task(self, task_uuid: str) -> ImportTask:
        task = self._get_task(task_uuid)
        task.status = ST_RUNNING
        task.phase = PHASE_PREPARING
        task.started_at = datetime.now()
        self.s.commit()

        # 维表/主数据（kind=dimension）：独立管线，不建螺旋节点
        spec = registry.require(task.dataset)
        if spec.kind == KIND_DIMENSION:
            return self.run_dimension_task(task, spec)

        # 导入模式处理
        if task.mode == MODE_REPLACE:
            removed = self._purge_dataset(task.dataset)
            logger.info("replace 模式已清空 dataset=%s 删除事实 %s 节点 %s",
                        task.dataset, removed[0], removed[1])
        existing: set[str] = set()
        if task.mode == MODE_DEDUPE:
            existing = self._load_fingerprints(task.dataset)
            logger.info("dedupe 模式预载已有指纹 %s 个 dataset=%s",
                        len(existing), task.dataset)

        self.store.ensure_roots()
        batch_size = get_param("import_batch_size")

        task.total = self._estimate_total(task)
        task.phase = PHASE_READING
        self.s.commit()

        row_iter = self._source_rows(task)

        facts_buffer: list[dict] = []
        deltas: dict[str, NodeDelta] = {}
        try:
            for row_no, raw in row_iter:
                task.processed += 1
                try:
                    rec = self.mapper.map_row(task.dataset, raw)
                    is_dup = (
                        task.mode == MODE_DEDUPE
                        and self._fingerprint(task.dataset, rec) in existing
                    )
                except CleanError as e:
                    task.failed += 1
                    self._add_error(task, row_no, str(e))
                except Exception as e:  # 未预期错误：隔离记录，不中断导入
                    task.failed += 1
                    self._add_error(task, row_no, f"未预期错误：{e}")
                    logger.exception("行映射未预期错误 row=%s", row_no)
                else:
                    if is_dup:
                        # 幂等：重复业务记录跳过，不累加、不落库
                        task.skipped += 1
                    else:
                        task.succeeded += 1
                        if task.mode == MODE_DEDUPE:
                            existing.add(self._fingerprint(task.dataset, rec))
                        facts_buffer.append(rec)
                        self._accumulate(deltas, task.dataset, rec)

                if len(facts_buffer) >= batch_size:
                    task.phase = PHASE_WRITING
                    self._flush_facts(task.dataset, facts_buffer)
                    self.store.apply_deltas(task.dataset, deltas)
                    facts_buffer.clear()
                    deltas.clear()
                    self.s.commit()
                    task.phase = PHASE_READING
                    self.s.commit()

            # 尾批
            task.phase = PHASE_WRITING
            if facts_buffer:
                self._flush_facts(task.dataset, facts_buffer)
            if deltas:
                self.store.apply_deltas(task.dataset, deltas)
            self.s.commit()

            task.phase = PHASE_FINALIZING
            self.s.commit()
            # 每次成功导入后重建注册数据集级产品交叉映射，并生成待审批提案。
            sync_product_crosswalk(self.s)
            create_feature_proposals(self.s)
            # 导入可能新增或替换节点；在物化 JSON 前回填物理双链拓扑。
            if spec.kind != "dimension":
                from engine.storage.topology import rebuild_dataset_topology
                rebuild_dataset_topology(self.s, task.dataset)
                # 阈值修剪：低于 growth_threshold 的节点不保留子层（系统参数页可调）
                pruned = self.store.prune_to_threshold(task.dataset)
                logger.info("阈值修剪 dataset=%s deleted=%d",
                            task.dataset, pruned["deleted_nodes"])
                # 修剪会删除旧节点；物化 JSON 前必须按剩余节点重新生成双链指针。
                rebuild_dataset_topology(self.s, task.dataset, force=True)

            # JSON 物化（先清空该数据集旧文件，保证磁盘与修剪后的树一致）
            task.phase = PHASE_MATERIALIZING
            self.s.commit()
            if spec.kind != "dimension":
                purge_dataset_json(task.dataset)
            self.store.materialize_json(task.dataset)

            task.status = ST_SUCCESS if task.failed == 0 else ST_PARTIAL
            task.phase = PHASE_COMPLETED
            task.finished_at = datetime.now()
            task.message = self._summary_message(task)
            self.s.commit()
            logger.info("导入任务结束 uuid=%s status=%s %s",
                        task.task_uuid, task.status, task.message)

        except Exception as e:
            task.status = ST_FAILED
            task.phase = PHASE_FAILED
            task.finished_at = datetime.now()
            task.message = f"导入失败：{e}"
            self.s.commit()
            logger.exception("导入任务异常终止 uuid=%s", task.task_uuid)
            raise

        return task

    # ---------------- 维表/主数据导入 ----------------

    def run_dimension_task(self, task: ImportTask, spec) -> ImportTask:
        """维表导入管线：按主键 upsert，不建螺旋节点、不写事实表。"""
        source = get_dim_source(task.dataset)
        if task.mode == MODE_REPLACE:
            removed = source.delete_all(self.s)
            self.s.commit()
            logger.info("维表 replace 清空 dataset=%s 删除 %s 行",
                        task.dataset, removed)

        task.total = self._estimate_total(task)
        task.phase = PHASE_READING
        self.s.commit()
        row_iter = self._source_rows(task)
        try:
            for row_no, raw in row_iter:
                task.processed += 1
                try:
                    rec = map_dim_row(spec, raw)
                except CleanError as e:
                    task.failed += 1
                    self._add_error(task, row_no, str(e))
                except Exception as e:  # 未预期错误：隔离记录，不中断导入
                    task.failed += 1
                    self._add_error(task, row_no, f"未预期错误：{e}")
                    logger.exception("维表行映射未预期错误 row=%s", row_no)
                else:
                    # 维表按主键幂等：dedupe/append 语义一致（同键覆盖）
                    source.upsert_many(self.s, [rec])
                    task.succeeded += 1

            task.phase = PHASE_FINALIZING
            self.s.commit()
            total = source.count(self.s)
            # 产品主数据单独导入时也要刷新事实数据的 product crosswalk。
            sync_product_crosswalk(self.s)
            create_feature_proposals(self.s)
            self.s.commit()
            task.status = ST_SUCCESS if task.failed == 0 else ST_PARTIAL
            task.phase = PHASE_COMPLETED
            task.finished_at = datetime.now()
            task.message = f"{self._summary_message(task)}，维表共 {total} 行"
            self.s.commit()
            logger.info("维表导入结束 uuid=%s status=%s %s",
                        task.task_uuid, task.status, task.message)
        except Exception as e:
            task.status = ST_FAILED
            task.phase = PHASE_FAILED
            task.finished_at = datetime.now()
            task.message = f"导入失败：{e}"
            self.s.commit()
            logger.exception("维表导入异常终止 uuid=%s", task.task_uuid)
            raise

        return task

    def _source_rows(self, task: ImportTask):
        """统一产出 (行号, 字典)：Excel 与数据库表共享后续映射/入库链路。"""
        if task.source_kind == "database":
            source = dict(task.source_ref or {})
            config = get_datasource_config(str(source.get("datasource_id") or ""))
            connector = create_connector(config)
            table = str(source.get("table") or "")

            def _rows():
                try:
                    for row_no, row in enumerate(connector.fetch_rows(table), start=2):
                        yield row_no, dict(row)
                finally:
                    connector.close()

            return _rows()
        reader = ExcelReader(task.file_path)
        _, row_iter = reader.read()
        return row_iter

    # ---------------- 节点聚合 ----------------

    def _accumulate(self, deltas: dict[str, NodeDelta], dataset: str, rec: dict) -> None:
        levels = physical_levels_for_dataset(dataset)
        metric_names = METRICS_BY_DATASET[dataset]
        # 层级取值键与指标来源均取自注册表声明，新增数据集无需改动本方法
        lvk = registry.level_value_key_of(dataset)
        metric_src = registry.metric_sources_of(dataset)
        # root 聚合
        root_d = deltas.setdefault(make_node_id(dataset, []), NodeDelta())
        root_d.count += 1
        for mname in metric_names:
            mv = rec.get(metric_src[mname])
            if mv is not None:
                root_d.metrics[mname] = root_d.metrics.get(mname, Decimal("0")) + mv

        values: list[str] = []
        for lv in levels:
            v = rec.get(lvk.get(lv, lv))
            if v in (None, ""):
                break
            values.append(str(v))
            nid = make_node_id(dataset, values)
            d = deltas.setdefault(nid, NodeDelta())
            d.count += 1
            for mname in metric_names:
                mv = rec.get(metric_src[mname])
                if mv is not None:
                    d.metrics[mname] = d.metrics.get(mname, Decimal("0")) + mv

    # ---------------- 事实表写入 ----------------

    def _flush_facts(self, dataset: str, records: list[dict]) -> None:
        get_fact_source(dataset).insert_many(self.s, records)

    # ---------------- 幂等与清空 ----------------

    @staticmethod
    def _fingerprint(dataset: str, rec: dict) -> str:
        """业务唯一指纹：同一业务粒度的行视为重复（键由注册表声明）。"""
        keys = registry.fingerprint_of(dataset)
        return "|".join(str(rec.get(k) or "") for k in keys)

    def _load_fingerprints(self, dataset: str) -> set[str]:
        keys = registry.fingerprint_of(dataset)
        rows = get_fact_source(dataset).fingerprint_values(self.s, keys)
        return {"|".join("" if v is None else str(v) for v in row)
                for row in rows}

    def _purge_dataset(self, dataset: str) -> tuple[int, int]:
        """清空某数据集事实表与全部螺旋节点（replace 模式）。"""
        fact_count = get_fact_source(dataset).delete_all(self.s)
        node_count = (
            self.s.query(SpiralNodeORM)
            .filter(SpiralNodeORM.dataset == dataset).delete()
        )
        self.s.commit()
        return fact_count, node_count

    # ---------------- 辅助 ----------------

    @staticmethod
    def _estimate_total(task: ImportTask) -> int:
        """尽量快速估计来源总行数；数据库来源暂不强制执行 COUNT。"""
        if task.source_kind != "excel" or not task.file_path:
            return 0
        try:
            result = probe_file(task.file_path)
            return max(int(result.get("row_count") or 0), 0)
        except Exception as exc:  # 估计失败不应阻断实际导入
            logger.warning("导入总行数估计失败 uuid=%s: %s", task.task_uuid, exc)
            return 0

    def _get_task(self, task_uuid: str) -> ImportTask:
        stmt = select(ImportTask).where(ImportTask.task_uuid == task_uuid)
        task = self.s.execute(stmt).scalar_one_or_none()
        if task is None:
            from engine.core.errors import NotFoundError
            raise NotFoundError("导入任务不存在", task_uuid)
        return task

    @staticmethod
    def _add_error(task: ImportTask, row_no: int, reason: str) -> None:
        if len(task.error_sample) < ERROR_SAMPLE_LIMIT:
            task.error_sample = [
                *task.error_sample,
                {"row": row_no, "reason": reason},
            ]

    @staticmethod
    def _summary_message(task: ImportTask) -> str:
        return (
            f"共处理 {task.processed} 条，成功 {task.succeeded}，"
            f"跳过 {task.skipped}，失败 {task.failed}"
        )
