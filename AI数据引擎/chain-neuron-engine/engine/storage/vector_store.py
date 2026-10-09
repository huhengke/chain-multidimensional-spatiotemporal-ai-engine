# -*- coding: utf-8 -*-
"""VectorStore：NumPy 向量 + JSON 元数据（产品特征点 / 毫毛）。"""
from __future__ import annotations

import json

import numpy as np

from engine.core.config import VECTORS_DIR
from engine.core.logging import get_logger

logger = get_logger("storage.vector")

VECTOR_FILE = VECTORS_DIR / "features.npy"
META_FILE = VECTORS_DIR / "features.json"


class VectorStore:
    def __init__(self, dim: int = 8):
        self.dim = dim
        self._vectors: np.ndarray | None = None
        self._meta: list[dict] = []
        self._load()

    def _load(self) -> None:
        if VECTOR_FILE.exists():
            self._vectors = np.load(VECTOR_FILE, allow_pickle=False)
        if META_FILE.exists():
            self._meta = json.loads(META_FILE.read_text(encoding="utf-8"))

    def add(self, vector: np.ndarray, meta: dict) -> int:
        vec = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if self._vectors is None:
            self._vectors = vec
        else:
            self._vectors = np.vstack([self._vectors, vec])
        idx = len(self._meta)
        self._meta.append(meta)
        return idx

    def search(self, query: np.ndarray, top_k: int = 5) -> list[dict]:
        if self._vectors is None or len(self._vectors) == 0:
            return []
        q = np.asarray(query, dtype=np.float32).reshape(1, -1)
        sims = (self._vectors @ q.T).reshape(-1)
        top = np.argsort(-sims)[:top_k]
        return [
            {**self._meta[int(i)], "score": float(sims[int(i)])}
            for i in top
        ]

    def save(self) -> None:
        VECTORS_DIR.mkdir(parents=True, exist_ok=True)
        if self._vectors is not None:
            np.save(VECTOR_FILE, self._vectors, allow_pickle=False)
        META_FILE.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def count(self) -> int:
        return 0 if self._vectors is None else len(self._vectors)
