from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from cnts_engine.catalog import CatalogAsset, canonical_json, sha256_file


_ASSET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def _validate_name(name: str) -> str:
    if not _ASSET_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid asset name: {name!r}")
    return name


def _publish_bytes(
    *,
    name: str,
    content: bytes,
    suffix: str,
    data_root: str | Path,
) -> CatalogAsset:
    name = _validate_name(name)
    if not suffix.startswith(".") or not suffix[1:].isalnum():
        raise ValueError(f"invalid asset suffix: {suffix!r}")
    data_root = Path(data_root).resolve()
    content_hash = hashlib.sha256(content).hexdigest()
    asset_id = f"{name}-{content_hash[:16]}"
    target = data_root / "assets" / name / asset_id / f"asset{suffix.lower()}"
    if target.exists():
        if sha256_file(target) != content_hash:
            raise RuntimeError(f"existing immutable asset is inconsistent: {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return CatalogAsset(
        asset_id=asset_id,
        relative_path=target.relative_to(data_root).as_posix(),
        content_hash=content_hash,
    )


def publish_file_asset(
    *,
    name: str,
    source: str | Path,
    data_root: str | Path,
) -> CatalogAsset:
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    return _publish_bytes(
        name=name,
        content=source.read_bytes(),
        suffix=source.suffix or ".bin",
        data_root=data_root,
    )


def publish_json_asset(
    *,
    name: str,
    payload: Mapping[str, Any],
    data_root: str | Path,
) -> CatalogAsset:
    return _publish_bytes(
        name=name,
        content=canonical_json(payload) + b"\n",
        suffix=".json",
        data_root=data_root,
    )


def publish_text_bundle_asset(
    *,
    name: str,
    sources: Mapping[str, str | Path],
    data_root: str | Path,
) -> CatalogAsset:
    files: dict[str, dict[str, str]] = {}
    for label, raw_path in sorted(sources.items()):
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        content = path.read_text(encoding="utf-8")
        files[label] = {
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
        }
    return publish_json_asset(
        name=name,
        payload={"bundle_version": 1, "files": files},
        data_root=data_root,
    )
