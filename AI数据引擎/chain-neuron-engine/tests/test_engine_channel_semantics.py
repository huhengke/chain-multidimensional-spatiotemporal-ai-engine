from __future__ import annotations

import unittest
from unittest.mock import patch

from agent.python_agent.engine_channel_client import EngineChannelClient


class EngineChannelSemanticTest(unittest.TestCase):
    def test_unmatched_configured_term_is_not_dropped(self) -> None:
        client = EngineChannelClient(base_url="http://127.0.0.1:9")
        try:
            with patch.object(client, "semantic_probe", return_value=(False, True)):
                filters, notes = client.confirm_base_terms(
                    ["oil_monthly_sales"],
                    {"organization": "不存在的公司"},
                )
        finally:
            client._client.close()

        self.assertEqual(filters, {"organization": "不存在的公司"})
        self.assertTrue(notes)
        self.assertIn("阻断", notes[0])

    def test_unconfigured_term_is_also_preserved_for_engine_rejection(self) -> None:
        client = EngineChannelClient(base_url="http://127.0.0.1:9")
        try:
            with patch.object(client, "semantic_probe", return_value=(False, False)):
                filters, notes = client.confirm_base_terms(
                    ["oil_monthly_sales"],
                    {"organization": "未声明地点"},
                )
        finally:
            client._client.close()

        self.assertEqual(filters, {"organization": "未声明地点"})
        self.assertIn("基座特征配置", notes[0])


if __name__ == "__main__":
    unittest.main()
