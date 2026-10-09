# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from engine.api.routes.imports import _task_dict
from engine.ingestion.batch_importer import BatchImporter


class ImportProgressTest(unittest.TestCase):
    def test_task_api_exposes_total_and_phase(self):
        task = SimpleNamespace(
            task_uuid="task-1",
            dataset="demo",
            file_name="demo.xlsx",
            source_kind="excel",
            source_ref={},
            mode="replace",
            status="running",
            phase="reading",
            total=21103,
            processed=2000,
            succeeded=2000,
            failed=0,
            skipped=0,
            error_sample=[],
            message=None,
            started_at=None,
            finished_at=None,
        )
        payload = _task_dict(task)
        self.assertEqual(payload["total"], 21103)
        self.assertEqual(payload["phase"], "reading")

    def test_excel_total_estimate_is_best_effort(self):
        task = SimpleNamespace(
            source_kind="excel",
            file_path="demo.xlsx",
            task_uuid="task-2",
        )
        with patch(
            "engine.ingestion.batch_importer.probe_file",
            return_value={"row_count": 21103},
        ):
            self.assertEqual(BatchImporter._estimate_total(task), 21103)

    def test_database_total_estimate_is_unknown_without_count_query(self):
        task = SimpleNamespace(
            source_kind="database",
            file_path="",
            task_uuid="task-3",
        )
        self.assertEqual(BatchImporter._estimate_total(task), 0)


if __name__ == "__main__":
    unittest.main()
