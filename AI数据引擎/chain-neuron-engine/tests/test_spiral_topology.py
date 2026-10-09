# -*- coding: utf-8 -*-
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.api.routes.spiral import _topology_slice_nodes
from engine.storage.database import Base
from engine.storage.models import SpiralNodeORM
from engine.storage.topology import rebuild_dataset_topology


class SpiralTopologyTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine, tables=[SpiralNodeORM.__table__])
        self.session = sessionmaker(bind=self.engine, future=True)()
        self.session.add_all([
            SpiralNodeORM(
                node_id="demo:root", dataset="demo", level=0, level_name="root",
                node_value="root", parent_id=None, path="demo/root",
                dimensions={}, record_count=3, aggregations={"amount": "3"},
            ),
            SpiralNodeORM(
                node_id="demo:2025", dataset="demo", level=1, level_name="year",
                node_value="2025", parent_id="demo:root", path="demo/2025",
                dimensions={"year": "2025"}, record_count=1, aggregations={"amount": "1"},
            ),
            SpiralNodeORM(
                node_id="demo:2026", dataset="demo", level=1, level_name="year",
                node_value="2026", parent_id="demo:root", path="demo/2026",
                dimensions={"year": "2026"}, record_count=2, aggregations={"amount": "2"},
            ),
        ])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_double_chain_preserves_semantic_tree(self):
        result = rebuild_dataset_topology(self.session, "demo")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.topology_version, 3)
        self.assertEqual(result.node_count, 3)
        root = self.session.get(SpiralNodeORM, "demo:root")
        self.assertEqual(root.topology_role, "base")
        self.assertEqual(root.temporal_layer, 0)
        self.assertEqual(root.time_bucket, "root")
        self.assertEqual(root.branch_depth, 0)
        self.assertEqual(root.feature_slots["spatial_layers"], ["foundation"])
        self.assertIsNone(root.strand_a_prev)
        self.assertEqual(root.strand_a_next, "demo:2025")
        self.assertEqual(root.strand_b_next, "demo:2025")
        # 不同时间圈不再互相配对；pair_node_id 只表达同一时间圈的对位。
        self.assertIsNone(self.session.get(SpiralNodeORM, "demo:2025").pair_node_id)
        self.assertIsNone(self.session.get(SpiralNodeORM, "demo:2026").pair_node_id)
        self.assertEqual(
            self.session.get(SpiralNodeORM, "demo:2026").parent_id,
            "demo:root",
        )
        self.assertEqual(
            self.session.get(SpiralNodeORM, "demo:2025").temporal_layer,
            1,
        )
        self.assertEqual(
            self.session.get(SpiralNodeORM, "demo:2025").feature_slots["spatial_layers"],
            ["inner", "store", "outer"],
        )

    def test_visual_slice_balances_branch_groups_and_keeps_parents(self):
        self.session.add(SpiralNodeORM(
            node_id="forest:root", dataset="forest", level=0, level_name="root",
            node_value="root", parent_id=None, path="forest/root",
            dimensions={}, record_count=240, aggregations={"amount": "240"},
            topology_version=2, topology_role="base", temporal_layer=0,
            helix_layer=0, branch_depth=0, ring_index=0,
        ))
        for layer in range(1, 9):
            parent_id = f"forest:t{layer}"
            self.session.add(SpiralNodeORM(
                node_id=parent_id, dataset="forest", level=1, level_name="day",
                node_value=f"2026-07-{layer:02d}", parent_id="forest:root",
                path=f"forest/{layer}", dimensions={"day": layer},
                record_count=30, aggregations={"amount": "30"},
                topology_version=2, topology_role="trunk", temporal_layer=layer,
                helix_layer=layer, time_bucket=f"day:{layer}", branch_depth=0,
                ring_index=layer,
            ))
            for branch_index in range(3):
                self.session.add(SpiralNodeORM(
                    node_id=f"{parent_id}:org{branch_index}", dataset="forest",
                    level=2, level_name="company", node_value=f"org{branch_index}",
                    parent_id=parent_id, path=f"forest/{layer}/org{branch_index}",
                    dimensions={"day": layer, "company": branch_index},
                    record_count=10, aggregations={"amount": "10"},
                    topology_version=2, topology_role="branch", temporal_layer=layer,
                    helix_layer=layer, time_bucket=f"day:{layer}", branch_depth=1,
                    growth_parent_id=parent_id, growth_dimension="company",
                    ring_index=branch_index,
                ))
        self.session.commit()

        items = _topology_slice_nodes(self.session, "forest", 24)
        selected_ids = {node.node_id for node in items}
        branches = [node for node in items if node.topology_role == "branch"]
        layers = {node.temporal_layer for node in branches}

        self.assertIn(1, layers)
        self.assertIn(8, layers)
        self.assertGreaterEqual(len(layers), 3)
        self.assertTrue(branches)
        self.assertTrue(all(node.growth_parent_id in selected_ids for node in branches))


if __name__ == "__main__":
    unittest.main()
