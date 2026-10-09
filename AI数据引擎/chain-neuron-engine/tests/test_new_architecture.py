from __future__ import annotations

import unittest

from engine.api.schemas_unified import UnifiedQueryRequest
from engine.model_config import public_model_config
from engine.storage.dataset_registry import registry


class NewArchitectureTest(unittest.TestCase):
    def test_query_plan_accepts_generic_intent(self):
        request = UnifiedQueryRequest(
            intent="sales",
            period="202608",
            dataset="oil_monthly_sales",
            group_by=["gascode"],
            metrics=["monthlyamount"],
        )
        self.assertEqual(request.intent, "sales")
        self.assertEqual(request.dataset, "oil_monthly_sales")

    def test_capabilities_are_metadata_driven(self):
        self.assertIn("oil_monthly_sales", registry.specs)
        self.assertIn("oil_sales_plan", registry.specs)
        self.assertIn("feature_layers", registry.require("oil_monthly_sales").capabilities)
        self.assertIn("feature_layers", registry.require("oil_sales_plan").capabilities)

    def test_model_config_does_not_expose_key(self):
        config = public_model_config()
        text = str(config)
        self.assertNotIn("api_key_env", text)
        for provider in config["providers"]:
            self.assertNotIn("api_key", provider)


if __name__ == "__main__":
    unittest.main()
