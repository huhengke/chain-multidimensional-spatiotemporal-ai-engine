from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import QueryPlan

try:
    from . import _native
except ImportError as exc:  # pragma: no cover - exercised before native build only
    _native = None
    _native_import_error = exc
else:
    _native_import_error = None


class CntsClient:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def _require_native() -> None:
        if _native is None:
            raise RuntimeError(
                "CNTS native extension is not installed; run scripts/bootstrap-python.ps1"
            ) from _native_import_error

    def verify(self) -> dict[str, Any]:
        self._require_native()
        return json.loads(_native.verify(str(self.root)))

    def verify_generation(self, generation_id: str) -> dict[str, Any]:
        self._require_native()
        return json.loads(_native.verify_generation(str(self.root), generation_id))

    def query(self, plan: QueryPlan | dict[str, Any]) -> dict[str, Any]:
        self._require_native()
        payload = plan.to_payload() if isinstance(plan, QueryPlan) else plan
        return json.loads(
            _native.query(
                str(self.root),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        )

    def query_generation(
        self,
        generation_id: str,
        plan: QueryPlan | dict[str, Any],
    ) -> dict[str, Any]:
        self._require_native()
        payload = plan.to_payload() if isinstance(plan, QueryPlan) else plan
        return json.loads(
            _native.query_generation(
                str(self.root),
                generation_id,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        )

    def topology_generation(
        self,
        generation_id: str,
        limit: int = 120,
    ) -> dict[str, Any]:
        self._require_native()
        return json.loads(
            _native.topology_generation(str(self.root), generation_id, int(limit))
        )

    @classmethod
    def generation_cache_info(cls) -> dict[str, Any]:
        cls._require_native()
        return json.loads(_native.generation_cache_info())

    @classmethod
    def clear_generation_cache(cls) -> None:
        cls._require_native()
        _native.clear_generation_cache()
