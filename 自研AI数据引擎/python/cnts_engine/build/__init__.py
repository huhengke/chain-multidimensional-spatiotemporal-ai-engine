"""Declaration-driven builders for CNTS fact trees and dimension assets."""

from .assets import publish_file_asset, publish_json_asset, publish_text_bundle_asset
from .exporter import FactExportResult, export_fact_dataset
from .models import FactSchema, SourceBinding, load_source_bindings
from .runner import DimensionBuildResult, FactBuildResult, build_dimension_asset, build_fact_tree
from .source import SourceSnapshot, SqliteTableSource

__all__ = [
    "DimensionBuildResult",
    "FactBuildResult",
    "FactExportResult",
    "FactSchema",
    "SourceBinding",
    "SourceSnapshot",
    "SqliteTableSource",
    "build_dimension_asset",
    "build_fact_tree",
    "export_fact_dataset",
    "load_source_bindings",
    "publish_file_asset",
    "publish_json_asset",
    "publish_text_bundle_asset",
]
