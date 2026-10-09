use std::collections::{BTreeMap, BTreeSet, HashSet};

use chrono::{Duration, NaiveDate};
use cnts_format::{
    stable_logical_node_id, CntsError, ColumnKind, ColumnValues, DatasetSchema, DetailColumn,
    DetailData, DictionarySet, FieldDictionary, InnerColumns, MetricColumn, OuterField, OuterIndex,
    PostingList, Result, StoreData, NONE_ORDINAL,
};

use crate::input::{dimension_names, NormalizedRecord};

const FLAG_ROOT: u16 = 1;
const FLAG_TIME: u16 = 2;
const FLAG_BRANCH: u16 = 4;
const FLAG_OVERCAPACITY: u16 = 8;

#[derive(Debug)]
pub struct BuildArtifacts {
    pub dictionaries: DictionarySet,
    pub inner: InnerColumns,
    pub store: StoreData,
    pub outer: OuterIndex,
    pub details: DetailData,
    pub row_count: u64,
}

#[derive(Debug, Clone)]
struct NodeBuild {
    logical_id: [u8; 16],
    parent: u64,
    first_child: u64,
    next_sibling: u64,
    axis_id: u32,
    value_id: u32,
    temporal_layer: u32,
    branch_depth: u16,
    flags: u16,
    start: usize,
    end: usize,
    time_path: String,
    dimension_path: String,
}

type Lookup = BTreeMap<String, BTreeMap<String, u32>>;

pub fn build_artifacts(
    schema: &DatasetSchema,
    mut records: Vec<NormalizedRecord>,
) -> Result<BuildArtifacts> {
    let dimensions = dimension_names(schema);
    records.sort_by(|left, right| {
        left.day.cmp(&right.day).then_with(|| {
            for field in &schema.growth_dimensions {
                let ordering = left.dimensions[field].cmp(&right.dimensions[field]);
                if !ordering.is_eq() {
                    return ordering;
                }
            }
            std::cmp::Ordering::Equal
        })
    });

    let (dictionaries, lookup) = build_dictionaries(&dimensions, &records);
    let details = build_details(schema, &dimensions, &lookup, &records)?;
    let outer = build_outer(schema, &lookup, &records)?;
    let mut nodes = Vec::new();
    let mut logical_ids = HashSet::new();
    let root_id = stable_logical_node_id(&schema.dataset_id, schema.schema_major, "*", "*");
    logical_ids.insert(root_id);
    nodes.push(NodeBuild {
        logical_id: root_id,
        parent: NONE_ORDINAL,
        first_child: NONE_ORDINAL,
        next_sibling: NONE_ORDINAL,
        axis_id: 0,
        value_id: 0,
        temporal_layer: 0,
        branch_depth: 0,
        flags: FLAG_ROOT,
        start: 0,
        end: records.len(),
        time_path: "*".to_string(),
        dimension_path: "*".to_string(),
    });

    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let mut time_start = 0_usize;
    let mut previous_time_node = NONE_ORDINAL;
    let mut temporal_layer = 1_u32;
    while time_start < records.len() {
        let day = records[time_start].day;
        let mut time_end = time_start + 1;
        while time_end < records.len() && records[time_end].day == day {
            time_end += 1;
        }
        let date = epoch
            .checked_add_signed(Duration::days(day as i64))
            .ok_or_else(|| CntsError::InvalidData(format!("day offset {day} is invalid")))?;
        let time_path = date.format("%Y-%m-%d").to_string();
        let logical_id =
            stable_logical_node_id(&schema.dataset_id, schema.schema_major, &time_path, "*");
        if !logical_ids.insert(logical_id) {
            return Err(CntsError::InvalidData(format!(
                "logical node id collision for {time_path}"
            )));
        }
        let ordinal = nodes.len() as u64;
        nodes.push(NodeBuild {
            logical_id,
            parent: 0,
            first_child: NONE_ORDINAL,
            next_sibling: NONE_ORDINAL,
            axis_id: 1,
            value_id: day as u32,
            temporal_layer,
            branch_depth: 0,
            flags: FLAG_TIME,
            start: time_start,
            end: time_end,
            time_path: time_path.clone(),
            dimension_path: "*".to_string(),
        });
        if nodes[0].first_child == NONE_ORDINAL {
            nodes[0].first_child = ordinal;
        }
        if previous_time_node != NONE_ORDINAL {
            nodes[previous_time_node as usize].next_sibling = ordinal;
        }
        previous_time_node = ordinal;
        grow_node(
            schema,
            &lookup,
            &records,
            &mut nodes,
            &mut logical_ids,
            ordinal,
            0,
        )?;
        time_start = time_end;
        temporal_layer += 1;
    }

    let inner = to_inner(&nodes)?;
    let store = build_store(schema, &records, &nodes)?;
    Ok(BuildArtifacts {
        dictionaries,
        inner,
        store,
        outer,
        details,
        row_count: records.len() as u64,
    })
}

fn build_dictionaries(
    dimensions: &[String],
    records: &[NormalizedRecord],
) -> (DictionarySet, Lookup) {
    let mut fields = Vec::new();
    let mut lookup = BTreeMap::new();
    for field in dimensions {
        let values: BTreeSet<String> = records
            .iter()
            .filter_map(|record| record.dimensions[field].clone())
            .collect();
        let values: Vec<String> = values.into_iter().collect();
        let field_lookup = values
            .iter()
            .enumerate()
            .map(|(index, value)| (value.clone(), index as u32))
            .collect();
        fields.push(FieldDictionary {
            field: field.clone(),
            values,
        });
        lookup.insert(field.clone(), field_lookup);
    }
    (DictionarySet { fields }, lookup)
}

fn null_bitmap(values: impl Iterator<Item = bool>, count: usize) -> Vec<u8> {
    let mut bitmap = vec![0_u8; count.div_ceil(8)];
    for (index, is_null) in values.enumerate() {
        if is_null {
            bitmap[index / 8] |= 1 << (index % 8);
        }
    }
    bitmap
}

fn build_details(
    schema: &DatasetSchema,
    dimensions: &[String],
    lookup: &Lookup,
    records: &[NormalizedRecord],
) -> Result<DetailData> {
    let row_count = records.len();
    let mut columns = vec![DetailColumn {
        name: schema.time_field.clone(),
        kind: ColumnKind::Time,
        scale: 0,
        null_bitmap: vec![0_u8; row_count.div_ceil(8)],
        values: ColumnValues::I32(records.iter().map(|record| record.day).collect()),
    }];
    for field in dimensions {
        let field_lookup = &lookup[field];
        let values = records
            .iter()
            .map(|record| {
                record.dimensions[field]
                    .as_ref()
                    .and_then(|value| field_lookup.get(value).copied())
                    .unwrap_or(u32::MAX)
            })
            .collect();
        columns.push(DetailColumn {
            name: field.clone(),
            kind: ColumnKind::Dictionary,
            scale: 0,
            null_bitmap: null_bitmap(
                records
                    .iter()
                    .map(|record| record.dimensions[field].is_none()),
                row_count,
            ),
            values: ColumnValues::U32(values),
        });
    }
    for metric in &schema.metrics {
        columns.push(DetailColumn {
            name: metric.name.clone(),
            kind: ColumnKind::Metric,
            scale: metric.scale,
            null_bitmap: vec![0_u8; row_count.div_ceil(8)],
            values: ColumnValues::I64(
                records
                    .iter()
                    .map(|record| record.metrics[&metric.name])
                    .collect(),
            ),
        });
    }
    let details = DetailData {
        row_count: row_count as u64,
        columns,
    };
    details.validate()?;
    Ok(details)
}

fn build_outer(
    schema: &DatasetSchema,
    lookup: &Lookup,
    records: &[NormalizedRecord],
) -> Result<OuterIndex> {
    let mut fields = Vec::new();
    for field in &schema.outer_dimensions {
        let mut by_value: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
        for (row_id, record) in records.iter().enumerate() {
            if let Some(value) = &record.dimensions[field] {
                let value_id = lookup[field][value];
                by_value.entry(value_id).or_default().push(row_id as u32);
            }
        }
        fields.push(OuterField {
            name: field.clone(),
            postings: by_value
                .into_iter()
                .map(|(value_id, rows)| PostingList { value_id, rows })
                .collect(),
        });
    }
    Ok(OuterIndex { fields })
}

fn grow_node(
    schema: &DatasetSchema,
    lookup: &Lookup,
    records: &[NormalizedRecord],
    nodes: &mut Vec<NodeBuild>,
    logical_ids: &mut HashSet<[u8; 16]>,
    parent: u64,
    dimension_index: usize,
) -> Result<()> {
    let (start, end, time_path, parent_path, temporal_layer, parent_depth) = {
        let node = &nodes[parent as usize];
        (
            node.start,
            node.end,
            node.time_path.clone(),
            node.dimension_path.clone(),
            node.temporal_layer,
            node.branch_depth,
        )
    };
    if (end - start) as u64 <= schema.growth_threshold {
        return Ok(());
    }
    if dimension_index >= schema.growth_dimensions.len() {
        nodes[parent as usize].flags |= FLAG_OVERCAPACITY;
        return Ok(());
    }
    let field = &schema.growth_dimensions[dimension_index];
    let axis_id = dimension_axis_id(schema, field)?;
    let mut groups = Vec::new();
    let mut group_start = start;
    while group_start < end {
        let value = records[group_start].dimensions[field].clone();
        let mut group_end = group_start + 1;
        while group_end < end && records[group_end].dimensions[field] == value {
            group_end += 1;
        }
        if let Some(value) = value {
            groups.push((value, group_start, group_end));
        }
        group_start = group_end;
    }
    if groups.is_empty() {
        return grow_node(
            schema,
            lookup,
            records,
            nodes,
            logical_ids,
            parent,
            dimension_index + 1,
        );
    }
    let mut previous = NONE_ORDINAL;
    for (value, child_start, child_end) in groups {
        let dimension_path = if parent_path == "*" {
            format!("{field}={value}")
        } else {
            format!("{parent_path}/{field}={value}")
        };
        let logical_id = stable_logical_node_id(
            &schema.dataset_id,
            schema.schema_major,
            &time_path,
            &dimension_path,
        );
        if !logical_ids.insert(logical_id) {
            return Err(CntsError::InvalidData(format!(
                "logical node id collision for {time_path}/{dimension_path}"
            )));
        }
        let ordinal = nodes.len() as u64;
        nodes.push(NodeBuild {
            logical_id,
            parent,
            first_child: NONE_ORDINAL,
            next_sibling: NONE_ORDINAL,
            axis_id,
            value_id: lookup[field][&value],
            temporal_layer,
            branch_depth: parent_depth + 1,
            flags: FLAG_BRANCH,
            start: child_start,
            end: child_end,
            time_path: time_path.clone(),
            dimension_path,
        });
        if nodes[parent as usize].first_child == NONE_ORDINAL {
            nodes[parent as usize].first_child = ordinal;
        }
        if previous != NONE_ORDINAL {
            nodes[previous as usize].next_sibling = ordinal;
        }
        previous = ordinal;
        grow_node(
            schema,
            lookup,
            records,
            nodes,
            logical_ids,
            ordinal,
            dimension_index + 1,
        )?;
    }
    Ok(())
}

fn dimension_axis_id(schema: &DatasetSchema, field: &str) -> Result<u32> {
    dimension_names(schema)
        .iter()
        .position(|name| name == field)
        .map(|index| index as u32 + 2)
        .ok_or_else(|| CntsError::InvalidData(format!("unknown growth dimension {field}")))
}

fn to_inner(nodes: &[NodeBuild]) -> Result<InnerColumns> {
    let inner = InnerColumns {
        logical_node_id: nodes.iter().map(|node| node.logical_id).collect(),
        parent_ordinal: nodes.iter().map(|node| node.parent).collect(),
        first_child_ordinal: nodes.iter().map(|node| node.first_child).collect(),
        next_sibling_ordinal: nodes.iter().map(|node| node.next_sibling).collect(),
        axis_id: nodes.iter().map(|node| node.axis_id).collect(),
        value_id: nodes.iter().map(|node| node.value_id).collect(),
        temporal_layer: nodes.iter().map(|node| node.temporal_layer).collect(),
        branch_depth: nodes.iter().map(|node| node.branch_depth).collect(),
        flags: nodes.iter().map(|node| node.flags).collect(),
        aggregate_offset: (0..nodes.len() as u64).collect(),
        outer_offset: nodes.iter().map(|_| 0_u64).collect(),
        detail_start: nodes.iter().map(|node| node.start as u64).collect(),
        detail_count: nodes
            .iter()
            .map(|node| (node.end - node.start) as u64)
            .collect(),
    };
    inner.validate()?;
    Ok(inner)
}

fn build_store(
    schema: &DatasetSchema,
    records: &[NormalizedRecord],
    nodes: &[NodeBuild],
) -> Result<StoreData> {
    let mut metrics = vec![MetricColumn {
        name: "__record_count".to_string(),
        scale: 0,
        values: nodes
            .iter()
            .map(|node| (node.end - node.start) as i128)
            .collect(),
    }];
    for metric in &schema.metrics {
        let mut values = Vec::with_capacity(nodes.len());
        for node in nodes {
            let mut total = 0_i128;
            for record in &records[node.start..node.end] {
                total = total
                    .checked_add(record.metrics[&metric.name] as i128)
                    .ok_or_else(|| CntsError::NumericOverflow(metric.name.clone()))?;
            }
            values.push(total);
        }
        metrics.push(MetricColumn {
            name: metric.name.clone(),
            scale: metric.scale,
            values,
        });
    }
    let store = StoreData {
        node_count: nodes.len() as u64,
        metrics,
    };
    store.validate()?;
    Ok(store)
}
