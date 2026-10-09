from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from cnts_engine.shadow.exporter import export_sqlite_to_jsonl
from cnts_engine.shadow.normalize import UNMAPPED_CATEGORY, UNMAPPED_ORG
from cnts_engine.shadow.parity import ParityCase, compare_batch_results
from cnts_engine.shadow.sqlite_source import SqliteOilDailySource


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE fact_oil_sales_daily (
            id INTEGER PRIMARY KEY,
            day TEXT,
            bigareacode TEXT,
            branchcode TEXT,
            areacode TEXT,
            oucode TEXT,
            category TEXT,
            gascode TEXT,
            salesvol TEXT,
            qty TEXT,
            amount TEXT,
            universalcode TEXT,
            shortname TEXT,
            salesmode TEXT,
            checkmode TEXT,
            branchname TEXT,
            areaname TEXT,
            bigareaname TEXT,
            price TEXT,
            guns TEXT,
            isoperate TEXT,
            datatype TEXT,
            remark TEXT
        )
        """
    )
    rows = [
        (
            1,
            "2026-08-01",
            "100047",
            "100047001",
            "100047001001",
            "100047001001012",
            "01",
            "60518726",
            "1.000001",
            "100.10",
            "900.01",
            "33350036",
            "示例区域甲示例节点站",
            "01",
            "012001",
            "示例区域甲组织层级二",
            "示例区域甲",
            "示例区域甲组织层级一",
            "9.00",
            "1",
            "0",
            "01",
            "0",
        ),
        (
            2,
            "2026-08-01",
            None,
            None,
            None,
            None,
            "01",
            "60518726",
            "2.100000",
            "200.20",
            "1800.02",
            "33351140",
            None,
            "01",
            "012001",
            None,
            None,
            None,
            "9.00",
            "2",
            "0",
            "01",
            "0",
        ),
        (
            3,
            "2026-08-02",
            "100047",
            "100047001",
            "100047001001",
            "100047001001012",
            None,
            "90325752",
            "0.000009",
            "0.70",
            "1.99",
            "33350036",
            "示例区域甲示例节点站",
            "01",
            "012001",
            "示例区域甲组织层级二",
            "示例区域甲",
            "示例区域甲组织层级一",
            "2.84",
            "1",
            "0",
            "01",
            "0",
        ),
    ]
    connection.executemany(
        """
        INSERT INTO fact_oil_sales_daily VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )
    connection.commit()
    connection.close()


def _create_candidates(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "dataset": "oil_sales_daily_shadow",
                "recommendations": [
                    {
                        "recommendation_id": "candidate-33351140",
                        "match": {"universalcode": "33351140"},
                        "candidate_mapping": {"oucode": "candidate-ou"},
                        "evidence": "fixture evidence",
                        "status": "pending_review",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def source_database(tmp_path: Path) -> Path:
    path = tmp_path / "source.db"
    _create_database(path)
    return path


def test_sqlite_source_is_read_only_and_chunked(source_database: Path) -> None:
    before = source_database.read_bytes()
    with SqliteOilDailySource(source_database) as source:
        assert source.fingerprint().row_count == 3
        assert [row["id"] for row in source.iter_rows(chunk_size=2)] == [1, 2, 3]
        with pytest.raises(sqlite3.OperationalError):
            source.connection.execute("CREATE TABLE forbidden (id INTEGER)")
    assert source_database.read_bytes() == before


def test_export_preserves_totals_and_surfaces_quality(
    source_database: Path, tmp_path: Path
) -> None:
    candidates = tmp_path / "candidates.json"
    _create_candidates(candidates)
    result = export_sqlite_to_jsonl(
        source_database, tmp_path / "run", candidates, chunk_size=1
    )

    records = [
        json.loads(line)
        for line in result.records_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 3
    assert records[1]["bigareacode"] == UNMAPPED_ORG
    assert records[1]["oucode"] == UNMAPPED_ORG
    assert records[1]["shortname"] == UNMAPPED_ORG
    assert records[1]["oucode"] != "candidate-ou"
    assert records[2]["category"] == UNMAPPED_CATEGORY
    assert records[0]["price"] == "9.00"
    assert records[0]["guns"] == "1"
    assert records[0]["datatype"] == "01"

    controls = json.loads(result.controls_path.read_text(encoding="utf-8"))
    assert controls["total"] == {
        "record_count": 3,
        "metrics": {
            "salesvol": "3.100010",
            "qty": "301.00",
            "amount": "2702.02",
        },
    }
    assert controls["quality"] == {
        "quality_event_count": 2,
        "organization_unmapped_rows": 1,
        "category_unmapped_rows": 1,
        "pending_review_rows": 1,
    }
    events = [
        json.loads(line)
        for line in result.quality_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["status"] == "pending_review"
    assert events[1]["status"] == "unresolved"
    recommendations = json.loads(
        result.recommendations_path.read_text(encoding="utf-8")
    )
    assert recommendations["recommendations"][0]["status"] == "pending_review"
    assert recommendations["recommendations"][0]["observed"]["rows"] == 1


def test_parity_comparison_is_exact() -> None:
    case = ParityCase(
        case_id="exact",
        plan={},
        expected_rows=[
            {
                "dimensions": {"category": "01"},
                "record_count": 1,
                "metrics": {
                    "salesvol": "1.000001",
                    "qty": "100.10",
                    "amount": "900.01",
                },
            }
        ],
        required_route="INTERSECT_OUTER",
        expected_fallback=True,
    )
    result = {
        "rows": deepcopy(case.expected_rows),
        "route": [{"operation": "INTERSECT_OUTER", "elapsed_micros": 5}],
        "detail_fallback": True,
    }
    assert compare_batch_results([case], [result])["passed"] is True

    result["rows"][0]["metrics"]["amount"] = "900.02"
    report = compare_batch_results([case], [result])
    assert report["passed"] is False
    assert report["mismatches"][0]["reasons"] == ["rows differ"]
