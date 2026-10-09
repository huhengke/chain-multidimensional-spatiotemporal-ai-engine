# -*- coding: utf-8 -*-
"""按时间圈重建中通周存的物理双链拓扑。

DNA 双链是物理连接，不是新的查询层级。``temporal_layer`` 表示时间圈，
``branch_depth`` 表示公共时间前缀之后的向外生长深度；``helix_layer`` 作为
旧 API 的兼容别名保留。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from engine.core.params import get_param
from engine.features.growth_plan import build_growth_plan
from engine.storage.dataset_registry import registry

TOPOLOGY_VERSION = 3


@dataclass
class TopologyStats:
    dataset: str
    topology_version: int
    node_count: int
    temporal_layers: int
    paired_nodes: int
    strand_a_edges: int
    strand_b_edges: int
    valid: bool
    errors: list[str]
    trunk_nodes: int = 0
    branch_nodes: int = 0
    grown_nodes: int = 0
    growth_threshold: int = 0
    physical_levels: list[str] = field(default_factory=list)

    @property
    def helix_layers(self) -> int:
        return self.temporal_layers

    def as_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "topology_version": self.topology_version,
            "node_count": self.node_count,
            "temporal_layers": self.temporal_layers,
            "helix_layers": self.temporal_layers,
            "paired_nodes": self.paired_nodes,
            "strand_a_edges": self.strand_a_edges,
            "strand_b_edges": self.strand_b_edges,
            "valid": self.valid,
            "errors": list(self.errors),
            "trunk_nodes": self.trunk_nodes,
            "branch_nodes": self.branch_nodes,
            "grown_nodes": self.grown_nodes,
            "growth_threshold": self.growth_threshold,
            "physical_levels": list(self.physical_levels),
            "spatial_layers": ["inner", "store", "outer"],
            "dna_is_topology_not_query_level": True,
            "query_path": [
                "foundation", "middle_inner", "middle_store",
                "outer_feature", "fact_result",
            ],
        }


def _scalar(session: Session, sql: str, params: dict) -> int:
    return int(session.execute(text(sql), params).scalar_one() or 0)


def _drop_temp_tables(session: Session) -> None:
    for name in ("topology_time", "topology_ring", "topology_a", "topology_b", "topology_pairs"):
        session.execute(text(f"DROP TABLE IF EXISTS {name}"))


def _pending_count(session: Session, dataset: str) -> int:
    return _scalar(session, """
        SELECT COUNT(*) FROM spiral_nodes
        WHERE dataset = :dataset
          AND (topology_version IS NULL OR topology_version != :version
               OR temporal_layer IS NULL OR time_bucket IS NULL
               OR branch_depth IS NULL OR ring_index IS NULL)
    """, {"dataset": dataset, "version": TOPOLOGY_VERSION})


def _time_prefix_count(dataset: str) -> int:
    """只计算语义路径最前面的连续时间层。"""
    spec = registry.get(dataset)
    if spec is None:
        # 单元测试/外部迁移可能只创建了 spiral_nodes 而没有注册表声明；
        # 这时按节点自身的层名做最小兼容推断，不阻断拓扑迁移。
        return 1
    return len(build_growth_plan(spec).trunk_levels)


def _time_expressions() -> tuple[str, str, str]:
    date_value = "NULLIF(CAST(json_extract(dimensions, '$.date') AS TEXT), '')"
    day_value = "NULLIF(CAST(json_extract(dimensions, '$.day') AS TEXT), '')"
    month_value = (
        "COALESCE(NULLIF(CAST(json_extract(dimensions, '$.month') AS TEXT), ''), "
        "NULLIF(CAST(json_extract(dimensions, '$.yyyymm') AS TEXT), ''))"
    )
    year_value = "NULLIF(CAST(json_extract(dimensions, '$.year') AS TEXT), '')"
    bucket = f"""
        CASE
          WHEN level = 0 THEN 'root'
          WHEN {date_value} GLOB '[0-9]*'
               AND length(replace(replace({date_value}, '-', ''), '/', '')) >= 8
            THEN 'date:' || substr(replace(replace({date_value}, '-', ''), '/', ''), 1, 8)
          WHEN {day_value} GLOB '[0-9]*' AND {month_value} GLOB '[0-9]*'
               AND length({month_value}) = 6
            THEN 'date:' || {month_value} || printf('%02d', CAST({day_value} AS INTEGER))
          WHEN {month_value} GLOB '[0-9]*' AND length({month_value}) = 6
            THEN 'month:' || {month_value}
          WHEN {month_value} GLOB '[0-9]*' AND {year_value} GLOB '[0-9]*'
            THEN 'month:' || printf('%04d%02d', CAST({year_value} AS INTEGER), CAST({month_value} AS INTEGER))
          WHEN {year_value} GLOB '[0-9]*'
            THEN 'year:' || printf('%04d', CAST({year_value} AS INTEGER))
          ELSE 'all'
        END
    """
    granularity = f"""
        CASE
          WHEN level = 0 THEN 'root'
          WHEN {date_value} GLOB '[0-9]*'
               AND length(replace(replace({date_value}, '-', ''), '/', '')) >= 8 THEN 'day'
          WHEN {day_value} GLOB '[0-9]*' AND {month_value} GLOB '[0-9]*' THEN 'day'
          WHEN {month_value} GLOB '[0-9]*' THEN 'month'
          WHEN {year_value} GLOB '[0-9]*' THEN 'year'
          ELSE 'all'
        END
    """
    sort_value = f"""
        CASE
          WHEN level = 0 THEN '0:00000000'
          WHEN {date_value} GLOB '[0-9]*'
               AND length(replace(replace({date_value}, '-', ''), '/', '')) >= 8
            THEN '1:' || substr(replace(replace({date_value}, '-', ''), '/', ''), 1, 8)
          WHEN {day_value} GLOB '[0-9]*' AND {month_value} GLOB '[0-9]*'
               AND length({month_value}) = 6
            THEN '1:' || {month_value} || printf('%02d', CAST({day_value} AS INTEGER))
          WHEN {month_value} GLOB '[0-9]*' AND length({month_value}) = 6
            THEN '1:' || {month_value} || '00'
          WHEN {month_value} GLOB '[0-9]*' AND {year_value} GLOB '[0-9]*'
            THEN '1:' || printf('%04d%02d00', CAST({year_value} AS INTEGER), CAST({month_value} AS INTEGER))
          WHEN {year_value} GLOB '[0-9]*'
            THEN '1:' || printf('%04d0000', CAST({year_value} AS INTEGER))
          ELSE '9:all'
        END
    """
    return bucket, granularity, sort_value


def rebuild_dataset_topology(
    session: Session,
    dataset: str,
    *,
    commit: bool = True,
    force: bool = False,
) -> TopologyStats:
    """按节点可识别的最细时间桶，批量生成物理双链。"""
    node_count = _scalar(session, "SELECT COUNT(*) FROM spiral_nodes WHERE dataset = :dataset", {"dataset": dataset})
    if node_count == 0:
        return TopologyStats(
            dataset=dataset,
            topology_version=TOPOLOGY_VERSION,
            node_count=0,
            temporal_layers=0,
            paired_nodes=0,
            strand_a_edges=0,
            strand_b_edges=0,
            valid=True,
            errors=[],
            growth_threshold=get_param("growth_threshold"),
        )
    if not force and _pending_count(session, dataset) == 0:
        return topology_summary(session, dataset, validate=True)

    _drop_temp_tables(session)
    params = {"dataset": dataset, "version": TOPOLOGY_VERSION}
    time_prefix_count = _time_prefix_count(dataset)
    bucket, granularity, sort_value = _time_expressions()

    session.execute(text(f"""
        CREATE TEMP TABLE topology_time AS
        WITH raw AS (
            SELECT node_id, level, {bucket} AS time_bucket,
                   {granularity} AS time_granularity, {sort_value} AS time_sort
            FROM spiral_nodes WHERE dataset = :dataset
        ), ranked AS (
            SELECT node_id, time_bucket, time_granularity, time_sort,
                   DENSE_RANK() OVER (ORDER BY time_sort, time_bucket) AS temporal_layer
            FROM raw WHERE level > 0
        )
        SELECT node_id, time_bucket, time_granularity, time_sort, 0 AS temporal_layer
        FROM raw WHERE level = 0
        UNION ALL
        SELECT node_id, time_bucket, time_granularity, time_sort, temporal_layer
        FROM ranked
    """), params)
    session.execute(text("CREATE INDEX topology_time_node_idx ON topology_time(node_id)"))

    session.execute(text("""
        CREATE TEMP TABLE topology_ring AS
        SELECT node_id, temporal_layer,
               CAST(ROW_NUMBER() OVER (
                   PARTITION BY dataset, temporal_layer
                   ORDER BY branch_depth, COALESCE(parent_id, ''), node_value, node_id
               ) - 1 AS INTEGER) AS ring_index
        FROM (
            SELECT n.*, t.temporal_layer,
                   CASE WHEN n.level = 0 THEN 0
                        ELSE MAX(n.level - :time_prefix_count, 0) END AS branch_depth
            FROM spiral_nodes n JOIN topology_time t ON t.node_id = n.node_id
            WHERE n.dataset = :dataset
        )
    """), {**params, "time_prefix_count": time_prefix_count})
    session.execute(text("CREATE INDEX topology_ring_node_idx ON topology_ring(node_id)"))

    session.execute(text("""
        UPDATE spiral_nodes
        SET topology_version = :version,
            temporal_layer = (SELECT temporal_layer FROM topology_time WHERE topology_time.node_id = spiral_nodes.node_id),
            helix_layer = (SELECT temporal_layer FROM topology_time WHERE topology_time.node_id = spiral_nodes.node_id),
            time_bucket = (SELECT time_bucket FROM topology_time WHERE topology_time.node_id = spiral_nodes.node_id),
            time_granularity = (SELECT time_granularity FROM topology_time WHERE topology_time.node_id = spiral_nodes.node_id),
            branch_depth = CASE WHEN level = 0 THEN 0 ELSE MAX(level - :time_prefix_count, 0) END,
            growth_parent_id = CASE WHEN level > :time_prefix_count THEN parent_id ELSE NULL END,
            growth_dimension = CASE WHEN level > :time_prefix_count THEN level_name ELSE NULL END,
            capacity_snapshot = record_count,
             growth_version = CASE WHEN level > :time_prefix_count THEN 1 ELSE 0 END,
            topology_role = CASE WHEN level = 0 THEN 'base' WHEN level > :time_prefix_count THEN 'branch' ELSE 'trunk' END,
            ring_index = (SELECT ring_index FROM topology_ring WHERE topology_ring.node_id = spiral_nodes.node_id),
            feature_slots = json_set(
                CASE WHEN feature_slots IS NULL OR json_valid(feature_slots) = 0 THEN '{}' ELSE feature_slots END,
                '$.semantic_role', CASE WHEN level = 0 THEN 'base' WHEN level > :time_prefix_count THEN 'branch' ELSE 'trunk' END,
                '$.temporal_layer', (SELECT temporal_layer FROM topology_time WHERE topology_time.node_id = spiral_nodes.node_id),
                '$.time_bucket', (SELECT time_bucket FROM topology_time WHERE topology_time.node_id = spiral_nodes.node_id),
                '$.time_granularity', (SELECT time_granularity FROM topology_time WHERE topology_time.node_id = spiral_nodes.node_id),
                '$.branch_depth', CASE WHEN level = 0 THEN 0 ELSE MAX(level - :time_prefix_count, 0) END,
                '$.ring_index', (SELECT ring_index FROM topology_ring WHERE topology_ring.node_id = spiral_nodes.node_id),
                '$.growth_parent_id', CASE WHEN level > :time_prefix_count THEN parent_id ELSE NULL END,
                '$.growth_dimension', CASE WHEN level > :time_prefix_count THEN level_name ELSE NULL END,
                '$.capacity_snapshot', record_count,
                '$.growth_version', CASE WHEN level > :time_prefix_count THEN 1 ELSE 0 END,
                '$.spatial_layers', CASE WHEN level = 0
                    THEN json('["foundation"]')
                    ELSE json('["inner","store","outer"]') END,
                '$.query_flow', CASE WHEN level = 0
                    THEN json('["normalize"]')
                    ELSE json('["inner","store","outer"]') END,
                '$.dna_is_topology_not_query_level', 1
            )
        WHERE dataset = :dataset
    """), {**params, "time_prefix_count": time_prefix_count})

    session.execute(text("""
        CREATE TEMP TABLE topology_a AS
        SELECT node_id,
               LAG(node_id) OVER (ORDER BY CASE WHEN level = 0 THEN 0 ELSE 1 END, path, level, node_id) AS prev_id,
               LEAD(node_id) OVER (ORDER BY CASE WHEN level = 0 THEN 0 ELSE 1 END, path, level, node_id) AS next_id
        FROM spiral_nodes WHERE dataset = :dataset
    """), params)
    session.execute(text("CREATE INDEX topology_a_node_idx ON topology_a(node_id)"))
    session.execute(text("""
        UPDATE spiral_nodes
        SET strand_a_prev = (SELECT prev_id FROM topology_a WHERE topology_a.node_id = spiral_nodes.node_id),
            strand_a_next = (SELECT next_id FROM topology_a WHERE topology_a.node_id = spiral_nodes.node_id)
        WHERE dataset = :dataset
    """), params)

    session.execute(text("""
        CREATE TEMP TABLE topology_b AS
        SELECT node_id,
               LAG(node_id) OVER (ORDER BY temporal_layer, ring_index, node_id) AS prev_id,
               LEAD(node_id) OVER (ORDER BY temporal_layer, ring_index, node_id) AS next_id
        FROM spiral_nodes WHERE dataset = :dataset
    """), params)
    session.execute(text("CREATE INDEX topology_b_node_idx ON topology_b(node_id)"))
    session.execute(text("""
        UPDATE spiral_nodes
        SET strand_b_prev = (SELECT prev_id FROM topology_b WHERE topology_b.node_id = spiral_nodes.node_id),
            strand_b_next = (SELECT next_id FROM topology_b WHERE topology_b.node_id = spiral_nodes.node_id)
        WHERE dataset = :dataset
    """), params)

    session.execute(text("""
        CREATE TEMP TABLE topology_pairs AS
        WITH ranked AS (
            SELECT node_id, dataset, temporal_layer, ring_index,
                   ROW_NUMBER() OVER (PARTITION BY dataset, temporal_layer ORDER BY ring_index, node_id) - 1 AS position,
                   COUNT(*) OVER (PARTITION BY dataset, temporal_layer) AS layer_count
            FROM spiral_nodes WHERE dataset = :dataset
        )
        SELECT left_node.node_id AS node_id, right_node.node_id AS pair_node_id
        FROM ranked AS left_node JOIN ranked AS right_node
          ON right_node.dataset = left_node.dataset
         AND right_node.temporal_layer = left_node.temporal_layer
         AND right_node.position = left_node.layer_count - 1 - left_node.position
        WHERE left_node.position <> right_node.position
    """), params)
    session.execute(text("CREATE INDEX topology_pairs_node_idx ON topology_pairs(node_id)"))
    session.execute(text("""
        UPDATE spiral_nodes
        SET pair_node_id = (SELECT pair_node_id FROM topology_pairs WHERE topology_pairs.node_id = spiral_nodes.node_id)
        WHERE dataset = :dataset
    """), params)

    _drop_temp_tables(session)
    if commit:
        session.commit()
    else:
        session.flush()
    return topology_summary(session, dataset, validate=True)


def rebuild_all_topologies(session: Session, datasets: list[str] | None = None, *, force: bool = False) -> list[TopologyStats]:
    if datasets is None:
        rows = session.execute(text("SELECT DISTINCT dataset FROM spiral_nodes ORDER BY dataset")).all()
        datasets = [row[0] for row in rows]
    return [rebuild_dataset_topology(session, dataset, force=force) for dataset in datasets]


def validate_dataset_topology(session: Session, dataset: str) -> list[str]:
    params = {"dataset": dataset, "version": TOPOLOGY_VERSION}
    errors: list[str] = []
    node_count = _scalar(session, "SELECT COUNT(*) FROM spiral_nodes WHERE dataset = :dataset", params)
    if node_count == 0:
        return errors
    incomplete = _scalar(session, """
        SELECT COUNT(*) FROM spiral_nodes
        WHERE dataset = :dataset AND (topology_version != :version OR topology_version IS NULL
          OR temporal_layer IS NULL OR time_bucket IS NULL OR branch_depth IS NULL OR ring_index IS NULL)
    """, params)
    if incomplete:
        errors.append(f"incomplete topology rows: {incomplete}")
    duplicate_slots = _scalar(session, """
        SELECT COUNT(*) FROM (
            SELECT temporal_layer, ring_index FROM spiral_nodes WHERE dataset = :dataset
            GROUP BY temporal_layer, ring_index HAVING COUNT(*) > 1
        )
    """, params)
    if duplicate_slots:
        errors.append(f"duplicate temporal slots: {duplicate_slots}")
    for label, next_col, prev_col in (("strand_a", "strand_a_next", "strand_a_prev"), ("strand_b", "strand_b_next", "strand_b_prev")):
        asym = _scalar(session, f"""
            SELECT COUNT(*) FROM spiral_nodes AS node
            LEFT JOIN spiral_nodes AS target ON target.dataset = node.dataset AND target.node_id = node.{next_col}
            WHERE node.dataset = :dataset AND node.{next_col} IS NOT NULL
              AND (target.node_id IS NULL OR target.{prev_col} <> node.node_id)
        """, params)
        if asym:
            errors.append(f"{label} asymmetric edges: {asym}")
        edges = _scalar(session, f"SELECT COUNT(*) FROM spiral_nodes WHERE dataset = :dataset AND {next_col} IS NOT NULL", params)
        heads = _scalar(session, f"SELECT COUNT(*) FROM spiral_nodes WHERE dataset = :dataset AND {prev_col} IS NULL", params)
        if edges != max(node_count - 1, 0) or heads != (1 if node_count else 0):
            errors.append(f"{label} is not one acyclic chain: edges={edges}, heads={heads}")
    pair_asym = _scalar(session, """
        SELECT COUNT(*) FROM spiral_nodes AS node
        LEFT JOIN spiral_nodes AS pair ON pair.dataset = node.dataset AND pair.node_id = node.pair_node_id
        WHERE node.dataset = :dataset AND node.pair_node_id IS NOT NULL
          AND (pair.node_id IS NULL OR pair.pair_node_id <> node.node_id OR pair.temporal_layer <> node.temporal_layer)
    """, params)
    if pair_asym:
        errors.append(f"pair relations asymmetric: {pair_asym}")
    return errors


def topology_summary(session: Session, dataset: str, *, validate: bool = False) -> TopologyStats:
    row = session.execute(text("""
        SELECT COALESCE(MAX(topology_version), 0), COUNT(*), COUNT(DISTINCT temporal_layer),
               COALESCE(SUM(CASE WHEN pair_node_id IS NOT NULL THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN strand_a_next IS NOT NULL THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN strand_b_next IS NOT NULL THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN topology_role = 'trunk' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN topology_role = 'branch' THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN grown = 1 THEN 1 ELSE 0 END), 0)
        FROM spiral_nodes WHERE dataset = :dataset
    """), {"dataset": dataset}).one()
    errors = validate_dataset_topology(session, dataset) if validate else []
    spec = registry.get(dataset)
    plan = build_growth_plan(spec) if spec is not None else None
    return TopologyStats(
        dataset=dataset, topology_version=int(row[0] or 0), node_count=int(row[1] or 0),
        temporal_layers=int(row[2] or 0), paired_nodes=int(row[3] or 0),
        strand_a_edges=int(row[4] or 0), strand_b_edges=int(row[5] or 0),
        valid=not errors, errors=errors,
        trunk_nodes=int(row[6] or 0), branch_nodes=int(row[7] or 0),
        grown_nodes=int(row[8] or 0), growth_threshold=get_param("growth_threshold"),
        physical_levels=list(plan.physical_levels) if plan is not None else [],
    )


class TopologyMigrator:
    def __init__(self, session: Session):
        self.session = session

    def migrate(self, datasets: list[str] | None = None) -> list[dict]:
        return [item.as_dict() for item in rebuild_all_topologies(self.session, datasets, force=True)]
