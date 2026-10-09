from __future__ import annotations

import json
from pathlib import Path

import pytest

from cnts_engine.build.assets import (
    publish_file_asset,
    publish_json_asset,
    publish_text_bundle_asset,
)
from cnts_engine.catalog import sha256_file


def test_assets_are_content_addressed_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    source.write_text("version: 1\n", encoding="utf-8")
    data_root = tmp_path / "data"
    first = publish_file_asset(
        name="foundation_features", source=source, data_root=data_root
    )
    second = publish_file_asset(
        name="foundation_features", source=source, data_root=data_root
    )
    assert first == second
    target = data_root / first.relative_path
    assert target.read_text(encoding="utf-8") == "version: 1\n"
    assert sha256_file(target) == first.content_hash


def test_text_bundle_preserves_each_source_with_hash(tmp_path: Path) -> None:
    one = tmp_path / "one.yaml"
    two = tmp_path / "two.yaml"
    one.write_text("one: 1\n", encoding="utf-8")
    two.write_text("two: 2\n", encoding="utf-8")
    asset = publish_text_bundle_asset(
        name="time_rules",
        sources={"one": one, "two": two},
        data_root=tmp_path / "data",
    )
    payload = json.loads((tmp_path / "data" / asset.relative_path).read_text())
    assert payload["files"]["one"]["content"] == "one: 1\n"
    assert len(payload["files"]["two"]["sha256"]) == 64


def test_json_asset_rejects_invalid_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid asset name"):
        publish_json_asset(name="../escape", payload={}, data_root=tmp_path)
