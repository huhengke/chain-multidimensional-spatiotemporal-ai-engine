# -*- coding: utf-8 -*-
"""scheduled_pull：APScheduler 定时从启用的外部数据源拉取（P1）。

拉取后复用与文件导入相同的映射/聚合链路（保证单一处理路径）。
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from engine.core.config import ENGINE_CONFIG_DIR, load_yaml_config
from engine.core.logging import get_logger

logger = get_logger("ingestion.schedule")

DS_FILE = ENGINE_CONFIG_DIR / "datasource.yaml"


class ScheduledPuller:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self._job_ids: list[str] = []

    def start(self) -> None:
        raw = load_yaml_config(DS_FILE)
        for ds in raw.get("datasources", []):
            if ds.get("enabled") and ds.get("cron"):
                job_id = f"pull-{ds['id']}"
                self.scheduler.add_job(
                    self._pull,
                    CronTrigger.from_crontab(ds["cron"]),
                    args=[ds],
                    id=job_id,
                    replace_existing=True,
                )
                self._job_ids.append(job_id)
                logger.info("注册定时拉取：%s cron=%s", ds["id"], ds["cron"])
        if self._job_ids:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    @staticmethod
    def _pull(ds_config: dict) -> None:
        """实际拉取：连接器 → 标准映射链路。

        MVP 阶段外部库默认 enabled=false；启用时需在此对接 DataMapper + SpiralStore
        （与 BatchImporter._flush_facts / _accumulate 完全一致的处理路径）。
        """
        from engine.ingestion.connectors.base import create_connector

        logger.info("开始定时拉取数据源：%s", ds_config.get("id"))
        connector = create_connector(ds_config)
        try:
            rows = connector.fetch_rows(ds_config.get("table", ""))
            count = 0
            for _ in rows:
                count += 1
                # TODO(M2 P1): 接入 DataMapper + 批量写库 + 节点聚合
            logger.info("数据源 %s 拉取 %d 行", ds_config.get("id"), count)
        finally:
            connector.close()
