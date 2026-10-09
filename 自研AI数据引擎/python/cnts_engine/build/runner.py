from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from cnts_engine.catalog import CatalogDataset, TimeRange, canonical_hash, sha256_file
from cnts_engine.client import CntsClient

from .exporter import FactExportResult, export_fact_dataset
from .models import FactSchema, SourceBinding
from .normalize import normalize_optional
from .source import SqliteTableSource


NativeBuilder = Callable[[Path, Path, Path], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class FactBuildResult:
    entry: CatalogDataset
    export: FactExportResult
    generation_manifest: dict[str, Any]
    provenance_path: Path


@dataclass(frozen=True, slots=True)
class DimensionBuildResult:
    entry: CatalogDataset
    asset_root: Path
    manifest_path: Path


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _subprocess_builder(binary: Path) -> NativeBuilder:
    def build(schema_path: Path, records_path: Path, dataset_root: Path) -> dict[str, Any]:
        completed = subprocess.run(
            [
                str(binary),
                "build",
                "--schema",
                str(schema_path),
                "--input",
                str(records_path),
                "--root",
                str(dataset_root),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("CNTS builder did not return a JSON object")
        return payload

    return build


def build_fact_tree(
    *,
    database: str | Path,
    schema_path: str | Path,
    binding: SourceBinding,
    data_root: str | Path,
    run_root: str | Path,
    cnts_binary: str | Path | None = None,
    native_builder: NativeBuilder | None = None,
    chunk_size: int = 10_000,
    delete_staging_records: bool = True,
) -> FactBuildResult:
    schema_path = Path(schema_path).resolve()
    schema = FactSchema.load(schema_path)
    data_root = Path(data_root).resolve()
    dataset_root = data_root / "datasets" / schema.dataset_id
    run_directory = (
        Path(run_root).resolve()
        / schema.dataset_id
        / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    export = export_fact_dataset(
        database,
        schema,
        binding,
        run_directory,
        chunk_size=chunk_size,
    )
    builder = native_builder
    if builder is None:
        if cnts_binary is None:
            raise ValueError("cnts_binary or native_builder is required")
        builder = _subprocess_builder(Path(cnts_binary).resolve())
    build_report = builder(schema_path, export.records_path, dataset_root)
    generation_id = str(build_report.get("generation_id") or "")
    if not generation_id:
        raise RuntimeError("CNTS builder returned no generation_id")
    manifest = CntsClient(dataset_root).verify_generation(generation_id)
    if manifest.get("dataset_id") != schema.dataset_id:
        raise RuntimeError("built generation dataset id does not match schema")
    if int(manifest.get("row_count", -1)) != export.row_count:
        raise RuntimeError("built generation row count does not match source export")
    schema_hash = canonical_hash(manifest["schema"])
    provenance = {
        "dataset_id": schema.dataset_id,
        "generation_id": generation_id,
        "schema_id": manifest["schema_id"],
        "schema_hash": schema_hash,
        "row_count": export.row_count,
        "source_fingerprint": export.source_fingerprint,
        "time_granularity": binding.time_granularity,
        "time_range": {"start": export.min_day, "end": export.max_day},
        "metric_controls": export.metric_controls,
    }
    provenance_path = dataset_root / "provenance" / f"{generation_id}.json"
    _write_json_atomic(provenance_path, provenance)
    entry = CatalogDataset(
        logical_id=schema.dataset_id,
        kind="fact",
        relative_path=dataset_root.relative_to(data_root).as_posix(),
        version_id=generation_id,
        schema_id=str(manifest["schema_id"]),
        schema_hash=schema_hash,
        manifest_hash=sha256_file(provenance_path),
        row_count=export.row_count,
        source_fingerprint=export.source_fingerprint,
        time_granularity=binding.time_granularity,  # type: ignore[arg-type]
        time_range=TimeRange(export.min_day, export.max_day),
        metric_controls=export.metric_controls,
    )
    if delete_staging_records:
        export.records_path.unlink(missing_ok=True)
    return FactBuildResult(
        entry=entry,
        export=export,
        generation_manifest=manifest,
        provenance_path=provenance_path,
    )


def build_dimension_asset(
    *,
    database: str | Path,
    schema_path: str | Path,
    binding: SourceBinding,
    data_root: str | Path,
    chunk_size: int = 10_000,
) -> DimensionBuildResult:
    if binding.kind != "dimension":
        raise ValueError("dimension source binding is required")
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    if schema.get("kind") != "dimension" or schema.get("dataset_id") != binding.dataset_id:
        raise ValueError("dimension schema and source binding do not match")
    columns = tuple(str(item) for item in schema.get("columns", []))
    if binding.key_column not in columns:
        raise ValueError("dimension primary key is not declared in schema columns")
    data_root = Path(data_root).resolve()
    temporary_root = data_root / "tmp" / f"{binding.dataset_id}-{uuid4().hex}"
    temporary_root.mkdir(parents=True, exist_ok=False)
    records_path = temporary_root / "records.jsonl"
    digest = hashlib.sha256()
    row_count = 0
    try:
        with SqliteTableSource(database, binding, columns) as source:
            before = source.snapshot()
            with records_path.open("w", encoding="utf-8", newline="\n") as records:
                for row in source.iter_rows(chunk_size=chunk_size):
                    payload = {column: normalize_optional(row[column]) for column in columns}
                    if payload[binding.key_column] is None:
                        raise ValueError("dimension primary key is null")
                    line = (
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    records.write(line)
                    digest.update(line.encode("utf-8"))
                    row_count += 1
            after = source.snapshot()
        if before != after:
            raise RuntimeError(f"source changed during export: {binding.dataset_id}")
        if row_count != before.row_count:
            raise RuntimeError("dimension export row count does not match source")
        source_fingerprint = digest.hexdigest()
        schema_hash = canonical_hash(schema)
        schema_id = schema_hash[:32]
        asset_id = f"{binding.dataset_id}-{source_fingerprint[:16]}"
        manifest = {
            "dataset_id": binding.dataset_id,
            "asset_id": asset_id,
            "schema_id": schema_id,
            "schema": schema,
            "row_count": row_count,
            "source_fingerprint": source_fingerprint,
            "files": [
                {
                    "name": records_path.name,
                    "bytes": records_path.stat().st_size,
                    "sha256": sha256_file(records_path),
                }
            ],
        }
        manifest_path = temporary_root / "manifest.json"
        _write_json_atomic(manifest_path, manifest)
        final_root = data_root / "datasets" / binding.dataset_id / "assets" / asset_id
        final_root.parent.mkdir(parents=True, exist_ok=True)
        if final_root.exists():
            existing_manifest = final_root / "manifest.json"
            existing_records = final_root / "records.jsonl"
            if (
                not existing_manifest.is_file()
                or not existing_records.is_file()
                or sha256_file(existing_manifest) != sha256_file(manifest_path)
                or sha256_file(existing_records) != sha256_file(records_path)
            ):
                raise RuntimeError(
                    f"existing immutable dimension asset is inconsistent: {final_root}"
                )
            shutil.rmtree(temporary_root)
        else:
            os.replace(temporary_root, final_root)
        final_manifest = final_root / "manifest.json"
        entry = CatalogDataset(
            logical_id=binding.dataset_id,
            kind="dimension",
            relative_path=final_root.relative_to(data_root).as_posix(),
            version_id=asset_id,
            schema_id=schema_id,
            schema_hash=schema_hash,
            manifest_hash=sha256_file(final_manifest),
            row_count=row_count,
            source_fingerprint=source_fingerprint,
        )
        return DimensionBuildResult(
            entry=entry,
            asset_root=final_root,
            manifest_path=final_manifest,
        )
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
