import tempfile
import unittest
from pathlib import Path

import yaml

from engine.features.contracts import build_dataset_contract
from engine.storage.dataset_registry import DatasetRegistry, registry


class DatasetSurfaceTest(unittest.TestCase):
    def test_public_dataset_types_and_sources_are_generic(self) -> None:
        snapshot = registry.raw()
        self.assertTrue(snapshot["datasets"])
        for item in snapshot["datasets"]:
            self.assertIn(item["kind"], {"generic", "dimension"})
            self.assertIn(item["source_origin"], {"base", "interface"})
            self.assertEqual(item["origin"], item["source_origin"])
            self.assertNotIn("builtin", item)
            self.assertNotIn("custom", item)

    def test_base_and_interface_are_read_only_and_editable_separately(self) -> None:
        items = {item["id"]: item for item in registry.raw()["datasets"]}
        # 基座声明只读、界面注册可编辑；注销/恢复的界面语义见下面那份临时注册表
        self.assertEqual(items["oil_master"]["source_origin"], "base")
        self.assertFalse(items["oil_master"]["editable"])
        self.assertFalse(items["oil_master"]["disabled"])
        self.assertNotIn("can_restore", items["oil_master"])
        self.assertEqual(items["non_oil_sales"]["source_origin"], "interface")
        self.assertTrue(items["non_oil_sales"]["editable"])
        self.assertTrue(items["non_oil_sales"]["can_unregister"])

    def test_agent_contract_does_not_expose_builtin_source_kind(self) -> None:
        contract = build_dataset_contract(registry.require("oil_monthly_sales"))
        self.assertEqual(contract["kind"], "generic")
        self.assertEqual(contract["source_origin"], "interface")
        self.assertNotEqual(contract["source"].get("kind"), "builtin")

    def test_base_unregister_is_reversible_without_changing_base_yaml(self) -> None:
        base_declaration = {
            "version": 1,
            "datasets": [{
                "id": "base_fact",
                "kind": "generic",
                "label": "基座事实",
                "fact_table": "fact_base_fact",
                "time": {"source": "月份", "format": "yyyymm"},
                "levels": ["year"],
                "level_labels": {"year": "年"},
                "level_value_key": {"year": "year"},
                "metrics": [{"name": "amount", "source": "amount", "column": "amount"}],
                "fact_columns": ["year", "amount"],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "datasets.yaml"
            custom_path = root / "datasets_custom.yaml"
            disabled_path = root / "datasets_disabled.yaml"
            base_path.write_text(yaml.safe_dump(base_declaration, allow_unicode=True), encoding="utf-8")
            custom_path.write_text("version: 1\ndatasets: []\n", encoding="utf-8")
            test_registry = DatasetRegistry(base_path, custom_path, disabled_path)

            original = base_path.read_text(encoding="utf-8")
            test_registry.disable_base("base_fact")
            disabled = {item["id"]: item for item in test_registry.raw()["datasets"]}["base_fact"]
            self.assertTrue(disabled["disabled"])
            self.assertTrue(disabled["can_restore"])
            self.assertNotIn("base_fact", test_registry.specs)
            self.assertNotIn(
                "base_fact",
                {item["id"] for item in test_registry.raw(include_disabled=False)["datasets"]},
            )
            self.assertEqual(base_path.read_text(encoding="utf-8"), original)

            test_registry.restore_base("base_fact")
            self.assertIn("base_fact", test_registry.specs)
            restored = {item["id"]: item for item in test_registry.raw()["datasets"]}["base_fact"]
            self.assertFalse(restored["disabled"])
            self.assertTrue(restored["can_unregister"])


if __name__ == "__main__":
    unittest.main()
