from pathlib import Path

import pytest

from cnts_engine import CntsClient, QueryPlan


DATA_ROOT = Path(__file__).parents[1] / "data" / "acceptance"


@pytest.mark.skipif(not (DATA_ROOT / "CURRENT").exists(), reason="generation not built")
def test_native_query_total() -> None:
    client = CntsClient(DATA_ROOT)
    generation_id = (DATA_ROOT / "CURRENT").read_text(encoding="utf-8").strip()
    result = client.query_generation(
        generation_id,
        QueryPlan(
            datasets=["demo_sales"],
            start="2026-08-01",
            end="2026-08-03",
            metrics=["sales_amount", "sales_volume"],
        )
    )
    assert result["rows"][0]["metrics"] == {
        "sales_amount": "2220.00",
        "sales_volume": "222.000",
    }
    assert result["detail_fallback"] is False


@pytest.mark.skipif(not (DATA_ROOT / "CURRENT").exists(), reason="generation not built")
def test_native_topology_generation_keeps_parent_closure() -> None:
    client = CntsClient(DATA_ROOT)
    generation_id = (DATA_ROOT / "CURRENT").read_text(encoding="utf-8").strip()
    payload = client.topology_generation(generation_id, limit=12)
    summary = payload["summary"]
    nodes = payload["nodes"]
    node_ids = {node["node_id"] for node in nodes}

    assert summary["dataset"] == "demo_sales"
    assert summary["node_count"] == 10
    assert summary["branch_nodes"] == 6
    assert len(nodes) <= 12
    assert nodes[0]["topology"]["role"] == "base"
    assert all(
        node["parent_id"] is None or node["parent_id"] in node_ids
        for node in nodes
    )
