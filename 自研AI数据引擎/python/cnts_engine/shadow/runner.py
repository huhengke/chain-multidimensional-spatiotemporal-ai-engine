from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from statistics import median
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .exporter import export_sqlite_to_jsonl
from .parity import run_parity
from .sqlite_source import TABLE, SqliteOilDailySource


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _assert_within(root: Path, target: Path) -> Path:
    root = root.resolve()
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"write target escapes project root: {target}") from error
    return target


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _peak_working_set(process_id: int) -> int | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, process_id)
    if not handle:
        return None
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), ctypes.sizeof(counters)
        ):
            return None
        return int(counters.PeakWorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def _run_json(
    arguments: list[str], *, measure_memory: bool = False
) -> tuple[dict[str, Any], float, int | None]:
    started = time.perf_counter()
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    peak = None
    while process.poll() is None:
        if measure_memory:
            measured = _peak_working_set(process.pid)
            if measured is not None:
                peak = max(peak or 0, measured)
        time.sleep(0.1)
    stdout, stderr = process.communicate()
    elapsed = time.perf_counter() - started
    if process.returncode != 0:
        raise RuntimeError(
            f"command failed ({process.returncode}): {' '.join(arguments)}\n"
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )
    return json.loads(stdout.decode("utf-8")), elapsed, peak


def _directory_sizes(path: Path) -> dict[str, int]:
    return {
        str(file.relative_to(path)): file.stat().st_size
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


def _source_storage(database: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "database_bytes": database.stat().st_size,
        "table_and_indexes_bytes": None,
        "objects": [],
    }
    try:
        with SqliteOilDailySource(database) as source:
            names = [
                str(row[0])
                for row in source.connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE tbl_name=? AND type IN ('table', 'index')
                    ORDER BY name
                    """,
                    (TABLE,),
                )
            ]
            placeholders = ",".join("?" for _ in names)
            rows = source.connection.execute(
                f"""
                SELECT name, SUM(pgsize) AS bytes
                FROM dbstat
                WHERE name IN ({placeholders})
                GROUP BY name
                ORDER BY name
                """,
                names,
            ).fetchall()
        objects = [{"name": str(row[0]), "bytes": int(row[1])} for row in rows]
        result["objects"] = objects
        result["table_and_indexes_bytes"] = sum(item["bytes"] for item in objects)
    except Exception as error:  # dbstat is optional in SQLite builds
        result["dbstat_error"] = str(error)
    return result


def _timing_summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = max(0, min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1))
    return {
        "min": ordered[0],
        "median": median(ordered),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def _benchmark_query(
    binary: Path,
    candidate_root: Path,
    controls_path: Path,
    run_directory: Path,
    *,
    cold_runs: int = 5,
    hot_runs: int = 50,
) -> dict[str, Any]:
    controls = json.loads(controls_path.read_text(encoding="utf-8"))
    plan = {
        "plan_version": 1,
        "datasets": ["oil_sales_daily_shadow"],
        "time_range": {
            "start": controls["source_before"]["min_day"],
            "end": controls["source_before"]["max_day"],
        },
        "filters": [],
        "group_by": [],
        "metrics": ["salesvol", "qty", "amount"],
        "comparisons": [],
        "merge_mode": "separate",
        "detail_policy": "allow_fallback",
    }
    plan_path = run_directory / "benchmark-plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    cold_milliseconds = []
    for _ in range(cold_runs):
        started = time.perf_counter()
        completed = subprocess.run(
            [
                str(binary),
                "query",
                "--root",
                str(candidate_root),
                "--plan",
                str(plan_path),
            ],
            capture_output=True,
            check=False,
        )
        elapsed = (time.perf_counter() - started) * 1000
        if completed.returncode != 0:
            raise RuntimeError(
                "cold query benchmark failed: "
                + completed.stderr.decode("utf-8", errors="replace")
            )
        json.loads(completed.stdout.decode("utf-8"))
        cold_milliseconds.append(elapsed)

    hot_plans_path = run_directory / "benchmark-hot-plans.json"
    hot_plans_path.write_text(
        json.dumps([plan] * hot_runs, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    started = time.perf_counter()
    completed = subprocess.run(
        [
            str(binary),
            "batch-query",
            "--root",
            str(candidate_root),
            "--plans",
            str(hot_plans_path),
        ],
        capture_output=True,
        check=False,
    )
    hot_elapsed = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise RuntimeError(
            "hot query benchmark failed: "
            + completed.stderr.decode("utf-8", errors="replace")
        )
    hot_results = json.loads(completed.stdout.decode("utf-8"))
    route_milliseconds = [
        sum(int(step["elapsed_micros"]) for step in result["route"]) / 1000
        for result in hot_results
    ]
    return {
        "query": "full-period-total",
        "cold_process_runs": cold_runs,
        "cold_process_milliseconds": _timing_summary(cold_milliseconds),
        "hot_batch_runs": hot_runs,
        "hot_batch_total_milliseconds": hot_elapsed,
        "hot_batch_amortized_milliseconds": hot_elapsed / hot_runs,
        "hot_engine_route_milliseconds": _timing_summary(route_milliseconds),
    }


def _route_summary(parity: dict[str, Any]) -> dict[str, Any]:
    cases = parity["cases"]
    case_count = len(cases)
    store = sum("READ_STORE" in case["route"] for case in cases)
    outer = sum("INTERSECT_OUTER" in case["route"] for case in cases)
    fallback = sum(bool(case["detail_fallback"]) for case in cases)
    return {
        "acceptance_case_count": case_count,
        "store_case_count": store,
        "outer_case_count": outer,
        "detail_fallback_case_count": fallback,
        "store_case_rate": store / case_count,
        "outer_case_rate": outer / case_count,
        "detail_fallback_case_rate": fallback / case_count,
        "scope": "acceptance matrix distribution, not production traffic hit rate",
    }


def _find_excel_files(source_database: Path) -> list[Path]:
    try:
        source_root = source_database.parents[2] / "数据源"
    except IndexError:
        return []
    if not source_root.is_dir():
        return []
    return sorted(source_root.glob("定位单元级业务域甲日报_*.xlsx"))


def _promote(candidate_root: Path, final_root: Path, generation_id: str) -> Path:
    source_generation = candidate_root / "generations" / generation_id
    if not source_generation.is_dir():
        raise RuntimeError("candidate generation directory is missing")
    generations = final_root / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    destination = generations / generation_id
    if destination.exists():
        raise RuntimeError(f"generation already exists: {destination}")
    os.replace(source_generation, destination)
    temporary = final_root / "CURRENT.tmp"
    with temporary.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"{generation_id}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, final_root / "CURRENT")
    return destination


def _check_expected(
    export: Any,
    *,
    expected_row_count: int | None,
    expected_start: str | None,
    expected_end: str | None,
    expected_org_unmapped: int | None,
    expected_category_unmapped: int | None,
    expected_pending_review: int | None,
) -> None:
    checks = [
        ("row_count", export.row_count, expected_row_count),
        ("min_day", export.source_before.min_day, expected_start),
        ("max_day", export.source_before.max_day, expected_end),
        (
            "organization_unmapped_rows",
            export.organization_unmapped_rows,
            expected_org_unmapped,
        ),
        (
            "category_unmapped_rows",
            export.category_unmapped_rows,
            expected_category_unmapped,
        ),
        ("pending_review_rows", export.pending_review_rows, expected_pending_review),
    ]
    differences = [
        f"{name}: expected {expected}, got {actual}"
        for name, actual, expected in checks
        if expected is not None and actual != expected
    ]
    if differences:
        raise RuntimeError("baseline check failed: " + "; ".join(differences))


def run_shadow_migration(
    source_database: str | Path,
    *,
    workspace: str | Path | None = None,
    cnts_binary: str | Path | None = None,
    schema: str | Path | None = None,
    candidates: str | Path | None = None,
    chunk_size: int = 10_000,
    expected_row_count: int | None = None,
    expected_start: str | None = None,
    expected_end: str | None = None,
    expected_org_unmapped: int | None = None,
    expected_category_unmapped: int | None = None,
    expected_pending_review: int | None = None,
) -> dict[str, Any]:
    workspace_path = Path(workspace or project_root()).resolve()
    output_root = _assert_within(
        workspace_path, workspace_path / "data" / "shadow" / "oil-sales-daily"
    )
    source_database = Path(source_database).resolve()
    schema_path = Path(
        schema or workspace_path / "schemas" / "oil-sales-daily-shadow.json"
    ).resolve()
    candidate_path = Path(
        candidates
        or workspace_path / "schemas" / "oil-sales-daily-pending-org-mappings.json"
    ).resolve()
    binary_path = Path(
        cnts_binary
        or workspace_path
        / "target"
        / "release"
        / ("cnts.exe" if os.name == "nt" else "cnts")
    ).resolve()
    for required in (source_database, schema_path, candidate_path, binary_path):
        if not required.is_file():
            raise FileNotFoundError(required)

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:8]}"
    run_directory = _assert_within(output_root, output_root / "runs" / run_id)
    run_directory.mkdir(parents=True, exist_ok=False)
    report_path = run_directory / "run-report.json"
    report: dict[str, Any] = {
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "source_database": str(source_database),
        "workspace": str(workspace_path),
        "old_system_cutover": False,
    }
    try:
        export_started = time.perf_counter()
        exported = export_sqlite_to_jsonl(
            source_database,
            run_directory,
            candidate_path,
            chunk_size=chunk_size,
        )
        report["export_seconds"] = time.perf_counter() - export_started
        report["export"] = exported.to_dict()
        _check_expected(
            exported,
            expected_row_count=expected_row_count,
            expected_start=expected_start,
            expected_end=expected_end,
            expected_org_unmapped=expected_org_unmapped,
            expected_category_unmapped=expected_category_unmapped,
            expected_pending_review=expected_pending_review,
        )

        candidate_root = run_directory / "candidate-root"
        build, build_seconds, peak_memory = _run_json(
            [
                str(binary_path),
                "build",
                "--schema",
                str(schema_path),
                "--input",
                str(exported.records_path),
                "--root",
                str(candidate_root),
            ],
            measure_memory=True,
        )
        report["build"] = build
        report["build_seconds"] = build_seconds
        report["build_peak_working_set_bytes"] = peak_memory

        inspect, inspect_seconds, _ = _run_json(
            [str(binary_path), "inspect", "--root", str(candidate_root)]
        )
        verify, verify_seconds, _ = _run_json(
            [str(binary_path), "verify", "--root", str(candidate_root)]
        )
        report["inspect"] = inspect
        report["verify"] = verify
        report["verify_seconds"] = verify_seconds
        report["inspect_seconds"] = inspect_seconds
        if int(inspect["row_count"]) != exported.row_count:
            raise RuntimeError("CNTS row count differs from exported row count")
        if int(verify["row_count"]) != exported.row_count:
            raise RuntimeError("verified manifest row count differs from source")

        parity = run_parity(
            binary_path, candidate_root, exported.controls_path, run_directory
        )
        report["parity"] = parity
        if not parity["passed"]:
            raise RuntimeError(
                f"parity failed: {parity['passed_count']}/{parity['case_count']} passed"
            )
        report["route_summary"] = _route_summary(parity)
        report["query_benchmark"] = _benchmark_query(
            binary_path,
            candidate_root,
            exported.controls_path,
            run_directory,
        )

        with SqliteOilDailySource(source_database) as source:
            final_fingerprint = source.fingerprint()
        report["source_final"] = final_fingerprint.to_dict()
        if final_fingerprint != exported.source_before:
            raise RuntimeError("source SQLite changed before shadow publication")

        generation_id = str(build["generation_id"])
        candidate_generation = candidate_root / "generations" / generation_id
        generation_sizes = _directory_sizes(candidate_generation)
        excel_files = _find_excel_files(source_database)
        source_storage = _source_storage(source_database)
        report["storage"] = {
            "source_database_bytes": source_storage["database_bytes"],
            "source_table_and_indexes_bytes": source_storage[
                "table_and_indexes_bytes"
            ],
            "source_objects": source_storage["objects"],
            "source_dbstat_error": source_storage.get("dbstat_error"),
            "staging_jsonl_bytes": exported.records_path.stat().st_size,
            "quality_sidecar_bytes": exported.quality_path.stat().st_size,
            "generation_bytes": sum(generation_sizes.values()),
            "generation_files": generation_sizes,
            "excel_files": [
                {"path": str(path), "bytes": path.stat().st_size}
                for path in excel_files
            ],
            "excel_total_bytes": sum(path.stat().st_size for path in excel_files),
        }
        if source_storage["table_and_indexes_bytes"]:
            report["storage"]["cnts_reduction_ratio"] = (
                source_storage["table_and_indexes_bytes"]
                / report["storage"]["generation_bytes"]
            )
        report["status"] = "ready_to_publish"
        report["report_path"] = str(report_path)
        _write_json_atomic(report_path, report)

        final_root = output_root / "engine"
        promoted = _promote(candidate_root, final_root, generation_id)
        report["promoted_generation"] = str(promoted)
        cleanup_errors = []
        try:
            exported.records_path.unlink()
        except OSError as error:
            cleanup_errors.append(f"staging JSONL: {error}")
        try:
            shutil.rmtree(candidate_root)
        except OSError as error:
            cleanup_errors.append(f"candidate root: {error}")
        report["cleanup"] = {
            "staging_jsonl_removed": not exported.records_path.exists(),
            "candidate_root_removed": not candidate_root.exists(),
            "errors": cleanup_errors,
        }
        report["status"] = "passed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        post_publish_errors = []
        try:
            _write_json_atomic(report_path, report)
        except OSError as error:
            post_publish_errors.append(f"final report: {error}")
        try:
            (output_root / "LATEST").write_text(f"{run_id}\n", encoding="ascii")
        except OSError as error:
            post_publish_errors.append(f"LATEST pointer: {error}")
        if post_publish_errors:
            report["post_publish_errors"] = post_publish_errors
            try:
                _write_json_atomic(report_path, report)
            except OSError:
                pass
        return report
    except Exception as error:
        report["status"] = "failed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["error"] = str(error)
        _write_json_atomic(report_path, report)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the oil daily CNTS shadow migration")
    parser.add_argument("--source-database", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--cnts-binary")
    parser.add_argument("--schema")
    parser.add_argument("--candidates")
    parser.add_argument("--chunk-size", type=int, default=10_000)
    parser.add_argument("--expected-row-count", type=int)
    parser.add_argument("--expected-start")
    parser.add_argument("--expected-end")
    parser.add_argument("--expected-org-unmapped", type=int)
    parser.add_argument("--expected-category-unmapped", type=int)
    parser.add_argument("--expected-pending-review", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    report = run_shadow_migration(
        arguments.source_database,
        workspace=arguments.workspace,
        cnts_binary=arguments.cnts_binary,
        schema=arguments.schema,
        candidates=arguments.candidates,
        chunk_size=arguments.chunk_size,
        expected_row_count=arguments.expected_row_count,
        expected_start=arguments.expected_start,
        expected_end=arguments.expected_end,
        expected_org_unmapped=arguments.expected_org_unmapped,
        expected_category_unmapped=arguments.expected_category_unmapped,
        expected_pending_review=arguments.expected_pending_review,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "run_id": report["run_id"],
                "row_count": report["export"]["row_count"],
                "generation_id": report["build"]["generation_id"],
                "parity": {
                    "passed": report["parity"]["passed"],
                    "passed_count": report["parity"]["passed_count"],
                    "case_count": report["parity"]["case_count"],
                },
                "report_path": report["report_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
