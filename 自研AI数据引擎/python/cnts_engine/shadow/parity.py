from __future__ import annotations

import calendar
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .normalize import METRIC_SCALES, decimal_to_scaled


DATASET_ID = "oil_sales_daily_shadow"
METRICS = list(METRIC_SCALES)


@dataclass(frozen=True, slots=True)
class ParityCase:
    case_id: str
    plan: dict[str, Any]
    expected_rows: list[dict[str, Any]]
    required_route: str | None = None
    expected_fallback: bool | None = None


def _plan(
    start: str,
    end: str,
    *,
    filters: list[dict[str, Any]] | None = None,
    group_by: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "plan_version": 1,
        "datasets": [DATASET_ID],
        "time_range": {"start": start, "end": end},
        "filters": filters or [],
        "group_by": group_by or [],
        "metrics": METRICS,
        "comparisons": [],
        "merge_mode": "separate",
        "detail_policy": "allow_fallback",
    }


def _group_rows(controls: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return controls["group_sets"][name]


def _without_dimensions(
    row: dict[str, Any], excluded: set[str]
) -> dict[str, Any]:
    return {
        "dimensions": {
            field: value
            for field, value in row["dimensions"].items()
            if field not in excluded
        },
        "record_count": row["record_count"],
        "metrics": row["metrics"],
    }


def _matching_group(
    controls: dict[str, Any], group_name: str, **dimensions: str
) -> list[dict[str, Any]]:
    return [
        _without_dimensions(row, set(dimensions))
        for row in _group_rows(controls, group_name)
        if all(row["dimensions"].get(field) == value for field, value in dimensions.items())
    ]


def build_parity_cases(controls: dict[str, Any]) -> list[ParityCase]:
    source = controls["source_before"]
    start = source["min_day"]
    end = source["max_day"]
    cases = [
        ParityCase(
            "total",
            _plan(start, end),
            [{"dimensions": {}, **controls["total"]}],
            "READ_STORE",
            False,
        )
    ]

    for row in _group_rows(controls, "day"):
        day = row["dimensions"]["day"]
        cases.append(
            ParityCase(
                f"day:{day}",
                _plan(day, day),
                [_without_dimensions(row, {"day"})],
                "READ_STORE",
                False,
            )
        )

    for row in _group_rows(controls, "month"):
        month = row["dimensions"]["month"]
        year_number, month_number = map(int, month.split("-"))
        month_start = date(year_number, month_number, 1).isoformat()
        month_end = date(
            year_number, month_number, calendar.monthrange(year_number, month_number)[1]
        ).isoformat()
        cases.append(
            ParityCase(
                f"month:{month}",
                _plan(month_start, month_end),
                [_without_dimensions(row, {"month"})],
                "READ_STORE",
                False,
            )
        )

    for field in (
        "bigareacode",
        "branchcode",
        "areacode",
        "oucode",
        "category",
        "gascode",
    ):
        cases.append(
            ParityCase(
                f"group:{field}",
                _plan(start, end, group_by=[field]),
                _group_rows(controls, field),
                "SCAN_DETAIL",
                True,
            )
        )

    for month_row in _group_rows(controls, "month"):
        month = month_row["dimensions"]["month"]
        year_number, month_number = map(int, month.split("-"))
        month_start = date(year_number, month_number, 1).isoformat()
        month_end = date(
            year_number, month_number, calendar.monthrange(year_number, month_number)[1]
        ).isoformat()
        cases.append(
            ParityCase(
                f"month-company:{month}",
                _plan(month_start, month_end, group_by=["bigareacode"]),
                _matching_group(controls, "month_bigareacode", month=month),
                "SCAN_DETAIL",
                True,
            )
        )
        cases.append(
            ParityCase(
                f"month-company-category:{month}",
                _plan(
                    month_start,
                    month_end,
                    group_by=["bigareacode", "category"],
                ),
                _matching_group(
                    controls, "month_bigareacode_category", month=month
                ),
                "SCAN_DETAIL",
                True,
            )
        )

    cases.append(
        ParityCase(
            "company:haikou",
            _plan(
                start,
                end,
                filters=[
                    {"field": "bigareacode", "op": "eq", "value": "100047"}
                ],
            ),
            _matching_group(controls, "bigareacode", bigareacode="100047"),
            "DESCEND_COMMON",
            False,
        )
    )
    tengfei_filters = [
        {"field": "bigareacode", "op": "eq", "value": "100047"},
        {"field": "branchcode", "op": "eq", "value": "100047001"},
        {"field": "areacode", "op": "eq", "value": "100047001001"},
        {"field": "oucode", "op": "eq", "value": "100047001001012"},
    ]
    cases.append(
        ParityCase(
            "station:haikou-tengfei",
            _plan(start, end, filters=tengfei_filters),
            _matching_group(controls, "oucode", oucode="100047001001012"),
            "FILTER_COMMON_DETAIL",
            True,
        )
    )
    cases.append(
        ParityCase(
            "quality:unmapped-org",
            _plan(
                start,
                end,
                filters=[
                    {
                        "field": "bigareacode",
                        "op": "eq",
                        "value": "__UNMAPPED_ORG__",
                    }
                ],
            ),
            _matching_group(
                controls, "bigareacode", bigareacode="__UNMAPPED_ORG__"
            ),
            "DESCEND_COMMON",
            False,
        )
    )
    cases.append(
        ParityCase(
            "quality:unmapped-category",
            _plan(
                start,
                end,
                filters=[
                    {
                        "field": "category",
                        "op": "eq",
                        "value": "__UNMAPPED_CATEGORY__",
                    }
                ],
            ),
            _matching_group(
                controls,
                "category",
                category="__UNMAPPED_CATEGORY__",
            ),
            "INTERSECT_OUTER",
            True,
        )
    )
    cases.append(
        ParityCase(
            "quality:gascode-90325752",
            _plan(
                start,
                end,
                filters=[{"field": "gascode", "op": "eq", "value": "90325752"}],
            ),
            _matching_group(controls, "gascode", gascode="90325752"),
            "INTERSECT_OUTER",
            True,
        )
    )
    cases.append(
        ParityCase(
            "empty:time-range",
            _plan("1900-01-01", "1900-01-02"),
            [],
            "READ_STORE",
            False,
        )
    )
    cases.append(
        ParityCase(
            "empty:dimension",
            _plan(
                start,
                end,
                filters=[
                    {
                        "field": "bigareacode",
                        "op": "eq",
                        "value": "__DOES_NOT_EXIST__",
                    }
                ],
            ),
            [],
            "DESCEND_COMMON",
            False,
        )
    )
    return cases


def _canonical_rows(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    canonical = []
    for row in rows:
        dimensions = tuple(sorted((str(k), str(v)) for k, v in row["dimensions"].items()))
        metrics = tuple(
            (name, decimal_to_scaled(row["metrics"][name], METRIC_SCALES[name]))
            for name in METRICS
        )
        canonical.append((dimensions, int(row["record_count"]), metrics))
    return sorted(canonical)


def compare_batch_results(
    cases: list[ParityCase], results: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(cases) != len(results):
        return {
            "passed": False,
            "case_count": len(cases),
            "passed_count": 0,
            "mismatches": [
                {
                    "case_id": "batch",
                    "reason": f"expected {len(cases)} results, received {len(results)}",
                }
            ],
            "cases": [],
        }
    mismatches = []
    summaries = []
    for case, result in zip(cases, results):
        reasons = []
        if _canonical_rows(case.expected_rows) != _canonical_rows(result["rows"]):
            reasons.append("rows differ")
        route = [step["operation"] for step in result.get("route", [])]
        if case.required_route is not None and case.required_route not in route:
            reasons.append(f"missing route {case.required_route}")
        if (
            case.expected_fallback is not None
            and bool(result.get("detail_fallback")) != case.expected_fallback
        ):
            reasons.append(
                f"detail_fallback expected {case.expected_fallback}, got {result.get('detail_fallback')}"
            )
        elapsed = sum(int(step.get("elapsed_micros", 0)) for step in result.get("route", []))
        summaries.append(
            {
                "case_id": case.case_id,
                "passed": not reasons,
                "row_groups": len(result.get("rows", [])),
                "route": route,
                "elapsed_micros": elapsed,
                "detail_fallback": bool(result.get("detail_fallback")),
            }
        )
        if reasons:
            mismatches.append(
                {
                    "case_id": case.case_id,
                    "reasons": reasons,
                    "expected": case.expected_rows,
                    "actual": result.get("rows", []),
                    "route": route,
                }
            )
    return {
        "passed": not mismatches,
        "case_count": len(cases),
        "passed_count": len(cases) - len(mismatches),
        "mismatches": mismatches,
        "cases": summaries,
    }


def run_parity(
    cnts_binary: str | Path,
    candidate_root: str | Path,
    controls_path: str | Path,
    run_directory: str | Path,
) -> dict[str, Any]:
    controls = json.loads(Path(controls_path).read_text(encoding="utf-8"))
    cases = build_parity_cases(controls)
    plans_path = Path(run_directory) / "parity-plans.json"
    plans_path.write_text(
        json.dumps([case.plan for case in cases], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    started = time.perf_counter()
    completed = subprocess.run(
        [
            str(Path(cnts_binary).resolve()),
            "batch-query",
            "--root",
            str(Path(candidate_root).resolve()),
            "--plans",
            str(plans_path.resolve()),
        ],
        capture_output=True,
        check=False,
    )
    elapsed_seconds = time.perf_counter() - started
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"CNTS batch query failed: {stderr.strip()}")
    results = json.loads(completed.stdout.decode("utf-8"))
    report = compare_batch_results(cases, results)
    report["batch_elapsed_seconds"] = elapsed_seconds
    report["plans_path"] = str(plans_path)
    report_path = Path(run_directory) / "parity-report.json"
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, report_path)
    report["report_path"] = str(report_path)
    return report
