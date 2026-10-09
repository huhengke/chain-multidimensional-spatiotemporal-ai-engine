from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from .models import CatalogManifest
from .validator import CatalogValidationReport, CatalogValidator


class CatalogConflictError(RuntimeError):
    pass


class CatalogStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def publish(
        self,
        catalog: CatalogManifest,
        validator: CatalogValidator,
    ) -> CatalogValidationReport:
        report = validator.validate(catalog)
        target = self._catalog_file(catalog.catalog_id)
        if target.exists():
            current = self.load(catalog.catalog_id)
            if current.catalog_hash == catalog.catalog_hash:
                return report
            raise CatalogConflictError(
                f"catalog {catalog.catalog_id} is immutable and already exists"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_json(target, catalog.to_dict())
        return report

    def load(self, catalog_id: str, *, expected_hash: str | None = None) -> CatalogManifest:
        path = self._catalog_file(catalog_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(f"catalog not found: {catalog_id}") from exc
        catalog = CatalogManifest.from_dict(raw)
        if expected_hash is not None and catalog.catalog_hash != expected_hash:
            raise CatalogConflictError(
                f"catalog hash mismatch: expected {expected_hash}, got {catalog.catalog_hash}"
            )
        return catalog

    def list(self) -> list[CatalogManifest]:
        if not self.root.exists():
            return []
        return [
            self.load(path.parent.name)
            for path in sorted(self.root.glob("*/catalog.json"))
        ]

    def _catalog_file(self, catalog_id: str) -> Path:
        # CatalogManifest validates ids; this protects direct load calls too.
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", catalog_id or ""):
            raise ValueError("invalid catalog id")
        return self.root / catalog_id / "catalog.json"

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
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
