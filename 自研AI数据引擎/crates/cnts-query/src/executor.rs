use std::{
    collections::{BTreeMap, BTreeSet},
    time::Instant,
};

use chrono::{Datelike, Duration, NaiveDate};
use cnts_format::{
    CntsError, ColumnValues, DetailPolicy, FilterOp, QueryFilter, QueryPlan, QueryResult,
    QueryResultRow, Result, RouteStep,
};

use crate::{loader::LoadedGeneration, planner::validate_plan};

struct GroupAccumulator {
    dimensions: BTreeMap<String, String>,
    metrics: BTreeMap<String, i128>,
    record_count: u64,
}

pub fn execute(generation: &LoadedGeneration, plan: &QueryPlan) -> Result<QueryResult> {
    let validated = validate_plan(plan, &generation.manifest.schema)?;
    let mut route = Vec::new();
    let time_started = Instant::now();
    let time_nodes = time_node_ordinals(generation, validated.start_day, validated.end_day);
    let time_matches = node_record_count(generation, &time_nodes)?;
    route.push(RouteStep {
        operation: "SEEK_TIME".to_string(),
        detail: format!("{}..{}", plan.time_range.start, plan.time_range.end),
        matched: time_matches,
        elapsed_micros: time_started.elapsed().as_micros() as u64,
    });

    if plan.filters.is_empty() && plan.group_by.is_empty() {
        return aggregate_nodes_from_store(
            generation,
            plan,
            &time_nodes,
            route,
            "materialized time nodes",
        );
    }

    if plan.group_by.is_empty() {
        if let Some(common_nodes) =
            materialized_common_nodes(generation, plan, &time_nodes, &mut route)?
        {
            return aggregate_nodes_from_store(
                generation,
                plan,
                &common_nodes,
                route,
                "materialized common-feature nodes",
            );
        }
    }

    if matches!(plan.detail_policy, DetailPolicy::AggregatesOnly) {
        return Err(CntsError::UnsupportedQuery(
            "query requires detail fallback but detail_policy=aggregates_only".to_string(),
        ));
    }

    let time_column = generation
        .details
        .column(&generation.manifest.schema.time_field)
        .ok_or_else(|| CntsError::InvalidData("time detail column is missing".to_string()))?;
    let days = match &time_column.values {
        ColumnValues::I32(values) => values,
        _ => {
            return Err(CntsError::InvalidData(
                "time column type mismatch".to_string(),
            ))
        }
    };
    let mut candidates: Vec<bool> = days
        .iter()
        .map(|day| *day >= validated.start_day && *day <= validated.end_day)
        .collect();

    for filter in &plan.filters {
        if generation
            .manifest
            .schema
            .outer_dimensions
            .contains(&filter.field)
        {
            apply_outer_filter(generation, filter, &mut candidates, &mut route)?;
        } else {
            apply_common_filter(generation, filter, &mut candidates, &mut route)?;
        }
    }
    aggregate_from_details(generation, plan, candidates, route)
}

fn time_node_ordinals(generation: &LoadedGeneration, start_day: i32, end_day: i32) -> Vec<usize> {
    let mut ordinals = Vec::new();
    for ordinal in 0..generation.inner.node_count() {
        if generation.inner.axis_id[ordinal] != 1 {
            continue;
        }
        let day = generation.inner.value_id[ordinal] as i32;
        if day >= start_day && day <= end_day {
            ordinals.push(ordinal);
        }
    }
    ordinals
}

fn node_record_count(generation: &LoadedGeneration, node_ordinals: &[usize]) -> Result<u64> {
    let count_metric = generation.store.metric("__record_count").ok_or_else(|| {
        CntsError::InvalidData("store record count metric is missing".to_string())
    })?;
    node_ordinals.iter().try_fold(0_u64, |total, ordinal| {
        let store_index = generation.inner.aggregate_offset[*ordinal] as usize;
        let value = u64::try_from(count_metric.values[store_index])
            .map_err(|_| CntsError::InvalidData("negative record count".to_string()))?;
        total
            .checked_add(value)
            .ok_or_else(|| CntsError::NumericOverflow("record_count".to_string()))
    })
}

fn aggregate_nodes_from_store(
    generation: &LoadedGeneration,
    plan: &QueryPlan,
    node_ordinals: &[usize],
    mut route: Vec<RouteStep>,
    detail: &str,
) -> Result<QueryResult> {
    let started = Instant::now();
    if node_ordinals.is_empty() {
        route.push(RouteStep {
            operation: "READ_STORE".to_string(),
            detail: format!("0 {detail}"),
            matched: 0,
            elapsed_micros: started.elapsed().as_micros() as u64,
        });
        return Ok(QueryResult {
            dataset_id: generation.manifest.dataset_id.clone(),
            generation_id: generation.manifest.generation_id.clone(),
            schema_id: generation.manifest.schema_id.clone(),
            rows: Vec::new(),
            route,
            detail_fallback: false,
        });
    }
    let mut metrics = BTreeMap::new();
    for metric_name in &plan.metrics {
        let metric = generation.store.metric(metric_name).ok_or_else(|| {
            CntsError::InvalidData(format!("store metric {metric_name} is missing"))
        })?;
        let mut total = 0_i128;
        for ordinal in node_ordinals {
            let store_index = generation.inner.aggregate_offset[*ordinal] as usize;
            total = total
                .checked_add(metric.values[store_index])
                .ok_or_else(|| CntsError::NumericOverflow(metric_name.clone()))?;
        }
        metrics.insert(metric_name.clone(), format_scaled(total, metric.scale));
    }
    let record_count = node_record_count(generation, node_ordinals)?;
    route.push(RouteStep {
        operation: "READ_STORE".to_string(),
        detail: format!("{} {detail}", node_ordinals.len()),
        matched: record_count,
        elapsed_micros: started.elapsed().as_micros() as u64,
    });
    Ok(QueryResult {
        dataset_id: generation.manifest.dataset_id.clone(),
        generation_id: generation.manifest.generation_id.clone(),
        schema_id: generation.manifest.schema_id.clone(),
        rows: vec![QueryResultRow {
            dimensions: BTreeMap::new(),
            metrics,
            record_count,
        }],
        route,
        detail_fallback: false,
    })
}

fn children_of(generation: &LoadedGeneration, parent: usize) -> Vec<usize> {
    let mut children = Vec::new();
    let mut ordinal = generation.inner.first_child_ordinal[parent];
    while ordinal != cnts_format::NONE_ORDINAL {
        children.push(ordinal as usize);
        ordinal = generation.inner.next_sibling_ordinal[ordinal as usize];
    }
    children
}

fn dimension_axis_id(generation: &LoadedGeneration, field: &str) -> Option<u32> {
    generation
        .manifest
        .schema
        .common_dimensions
        .iter()
        .chain(generation.manifest.schema.outer_dimensions.iter())
        .position(|name| name == field)
        .map(|index| index as u32 + 2)
}

fn materialized_common_nodes(
    generation: &LoadedGeneration,
    plan: &QueryPlan,
    time_nodes: &[usize],
    route: &mut Vec<RouteStep>,
) -> Result<Option<Vec<usize>>> {
    if plan.filters.is_empty()
        || plan.filters.iter().any(|filter| {
            generation
                .manifest
                .schema
                .outer_dimensions
                .contains(&filter.field)
        })
        || plan.filters.len() > generation.manifest.schema.growth_dimensions.len()
    {
        return Ok(None);
    }
    let mut ordered_filters = Vec::new();
    for field in generation
        .manifest
        .schema
        .growth_dimensions
        .iter()
        .take(plan.filters.len())
    {
        let Some(filter) = plan.filters.iter().find(|filter| &filter.field == field) else {
            return Ok(None);
        };
        ordered_filters.push(filter);
    }

    let started = Instant::now();
    let mut current = time_nodes.to_vec();
    for filter in ordered_filters {
        let requested = filter_values(filter)?;
        let dictionary = generation
            .dictionaries
            .field(&filter.field)
            .ok_or_else(|| {
                CntsError::InvalidData(format!("dictionary {} is missing", filter.field))
            })?;
        let ids: BTreeSet<u32> = dictionary
            .values
            .iter()
            .enumerate()
            .filter(|(_, value)| requested.contains(*value))
            .map(|(index, _)| index as u32)
            .collect();
        let axis_id = dimension_axis_id(generation, &filter.field).ok_or_else(|| {
            CntsError::InvalidData(format!("dimension axis {} is missing", filter.field))
        })?;
        let mut next = Vec::new();
        for parent in &current {
            let children = children_of(generation, *parent);
            let axis_children: Vec<usize> = children
                .into_iter()
                .filter(|child| generation.inner.axis_id[*child] == axis_id)
                .collect();
            if axis_children.is_empty() {
                return Ok(None);
            }
            next.extend(
                axis_children
                    .into_iter()
                    .filter(|child| ids.contains(&generation.inner.value_id[*child])),
            );
        }
        current = next;
        if current.is_empty() {
            break;
        }
    }
    let matched = node_record_count(generation, &current)?;
    route.push(RouteStep {
        operation: "DESCEND_COMMON".to_string(),
        detail: format!("{} materialized common filters", plan.filters.len()),
        matched,
        elapsed_micros: started.elapsed().as_micros() as u64,
    });
    Ok(Some(current))
}

fn filter_values(filter: &QueryFilter) -> Result<BTreeSet<String>> {
    match filter.op {
        FilterOp::Eq => Ok([filter.value.as_str().unwrap().to_string()]
            .into_iter()
            .collect()),
        FilterOp::In => filter
            .value
            .as_array()
            .unwrap()
            .iter()
            .map(|value| {
                value.as_str().map(str::to_string).ok_or_else(|| {
                    CntsError::UnsupportedQuery(format!(
                        "in filter {} contains a non-string value",
                        filter.field
                    ))
                })
            })
            .collect(),
    }
}

fn apply_outer_filter(
    generation: &LoadedGeneration,
    filter: &QueryFilter,
    candidates: &mut [bool],
    route: &mut Vec<RouteStep>,
) -> Result<()> {
    let started = Instant::now();
    let requested = filter_values(filter)?;
    let dictionary = generation
        .dictionaries
        .field(&filter.field)
        .ok_or_else(|| CntsError::InvalidData(format!("dictionary {} is missing", filter.field)))?;
    let ids: BTreeSet<u32> = dictionary
        .values
        .iter()
        .enumerate()
        .filter(|(_, value)| requested.contains(*value))
        .map(|(index, _)| index as u32)
        .collect();
    let field = generation.outer.field(&filter.field).ok_or_else(|| {
        CntsError::InvalidData(format!("outer index {} is missing", filter.field))
    })?;
    let mut posting_matches = vec![false; candidates.len()];
    for posting in &field.postings {
        if ids.contains(&posting.value_id) {
            for row in &posting.rows {
                if let Some(slot) = posting_matches.get_mut(*row as usize) {
                    *slot = true;
                }
            }
        }
    }
    for (candidate, posting_match) in candidates.iter_mut().zip(posting_matches) {
        *candidate &= posting_match;
    }
    let matched = candidates.iter().filter(|matched| **matched).count() as u64;
    route.push(RouteStep {
        operation: "INTERSECT_OUTER".to_string(),
        detail: format!("{}={:?}", filter.field, requested),
        matched,
        elapsed_micros: started.elapsed().as_micros() as u64,
    });
    Ok(())
}

fn apply_common_filter(
    generation: &LoadedGeneration,
    filter: &QueryFilter,
    candidates: &mut [bool],
    route: &mut Vec<RouteStep>,
) -> Result<()> {
    let started = Instant::now();
    let requested = filter_values(filter)?;
    let dictionary = generation
        .dictionaries
        .field(&filter.field)
        .ok_or_else(|| CntsError::InvalidData(format!("dictionary {} is missing", filter.field)))?;
    let ids: BTreeSet<u32> = dictionary
        .values
        .iter()
        .enumerate()
        .filter(|(_, value)| requested.contains(*value))
        .map(|(index, _)| index as u32)
        .collect();
    let column = generation.details.column(&filter.field).ok_or_else(|| {
        CntsError::InvalidData(format!("detail column {} is missing", filter.field))
    })?;
    let values = match &column.values {
        ColumnValues::U32(values) => values,
        _ => {
            return Err(CntsError::InvalidData(format!(
                "dimension column {} type mismatch",
                filter.field
            )))
        }
    };
    for (candidate, value_id) in candidates.iter_mut().zip(values) {
        *candidate &= ids.contains(value_id);
    }
    let matched = candidates.iter().filter(|matched| **matched).count() as u64;
    route.push(RouteStep {
        operation: "FILTER_COMMON_DETAIL".to_string(),
        detail: format!("{}={:?}", filter.field, requested),
        matched,
        elapsed_micros: started.elapsed().as_micros() as u64,
    });
    Ok(())
}

fn aggregate_from_details(
    generation: &LoadedGeneration,
    plan: &QueryPlan,
    candidates: Vec<bool>,
    mut route: Vec<RouteStep>,
) -> Result<QueryResult> {
    let started = Instant::now();
    let time_column = generation
        .details
        .column(&generation.manifest.schema.time_field)
        .ok_or_else(|| CntsError::InvalidData("time detail column is missing".to_string()))?;
    let days = match &time_column.values {
        ColumnValues::I32(values) => values,
        _ => {
            return Err(CntsError::InvalidData(
                "time column type mismatch".to_string(),
            ))
        }
    };
    let mut dimension_columns = Vec::new();
    for field in &plan.group_by {
        if is_virtual_time_group(generation, field) {
            continue;
        }
        let column = generation
            .details
            .column(field)
            .ok_or_else(|| CntsError::InvalidData(format!("group column {field} is missing")))?;
        let values = match &column.values {
            ColumnValues::U32(values) => values,
            _ => {
                return Err(CntsError::InvalidData(format!(
                    "group column {field} type mismatch"
                )))
            }
        };
        let dictionary = generation.dictionaries.field(field).ok_or_else(|| {
            CntsError::InvalidData(format!("group dictionary {field} is missing"))
        })?;
        dimension_columns.push((field, values, dictionary));
    }
    let mut metric_columns = Vec::new();
    for metric in &plan.metrics {
        let column = generation
            .details
            .column(metric)
            .ok_or_else(|| CntsError::InvalidData(format!("metric column {metric} is missing")))?;
        let values = match &column.values {
            ColumnValues::I64(values) => values,
            _ => {
                return Err(CntsError::InvalidData(format!(
                    "metric column {metric} type mismatch"
                )))
            }
        };
        metric_columns.push((metric, column.scale, values));
    }
    let mut groups: BTreeMap<Vec<String>, GroupAccumulator> = BTreeMap::new();
    for (row, selected) in candidates.iter().enumerate() {
        if !selected {
            continue;
        }
        let key = plan
            .group_by
            .iter()
            .map(|field| {
                if is_virtual_time_group(generation, field) {
                    return format_time_group(
                        field,
                        &generation.manifest.schema.time_field,
                        days[row],
                    );
                }
                let (_, values, dictionary) = dimension_columns
                    .iter()
                    .find(|(name, _, _)| *name == field)
                    .ok_or_else(|| {
                        CntsError::InvalidData(format!("group column {field} was not prepared"))
                    })?;
                Ok(dictionary
                    .values
                    .get(values[row] as usize)
                    .cloned()
                    .unwrap_or_else(|| "<null>".to_string()))
            })
            .collect::<Result<Vec<_>>>()?;
        let accumulator = groups.entry(key.clone()).or_insert_with(|| {
            let dimensions = plan
                .group_by
                .iter()
                .cloned()
                .zip(key.iter().cloned())
                .collect();
            GroupAccumulator {
                dimensions,
                metrics: plan
                    .metrics
                    .iter()
                    .map(|name| (name.clone(), 0_i128))
                    .collect(),
                record_count: 0,
            }
        });
        accumulator.record_count += 1;
        for (metric, _, values) in &metric_columns {
            let slot = accumulator.metrics.get_mut(*metric).unwrap();
            *slot = slot
                .checked_add(values[row] as i128)
                .ok_or_else(|| CntsError::NumericOverflow((*metric).clone()))?;
        }
    }
    let rows = groups
        .into_values()
        .map(|group| {
            let metrics = metric_columns
                .iter()
                .map(|(name, scale, _)| {
                    ((*name).clone(), format_scaled(group.metrics[*name], *scale))
                })
                .collect();
            QueryResultRow {
                dimensions: group.dimensions,
                metrics,
                record_count: group.record_count,
            }
        })
        .collect::<Vec<_>>();
    let matched = rows.iter().map(|row| row.record_count).sum();
    route.push(RouteStep {
        operation: "SCAN_DETAIL".to_string(),
        detail: format!("{} group fields", plan.group_by.len()),
        matched,
        elapsed_micros: started.elapsed().as_micros() as u64,
    });
    Ok(QueryResult {
        dataset_id: generation.manifest.dataset_id.clone(),
        generation_id: generation.manifest.generation_id.clone(),
        schema_id: generation.manifest.schema_id.clone(),
        rows,
        route,
        detail_fallback: true,
    })
}

fn is_virtual_time_group(generation: &LoadedGeneration, field: &str) -> bool {
    let schema = &generation.manifest.schema;
    let is_dimension = schema.common_dimensions.iter().any(|name| name == field)
        || schema.outer_dimensions.iter().any(|name| name == field);
    !is_dimension
        && (field == schema.time_field
            || matches!(
                field,
                "year"
                    | "half_year"
                    | "quarter"
                    | "season"
                    | "month"
                    | "month_parity"
                    | "week"
                    | "weekday_type"
                    | "day"
            ))
}

fn format_time_group(field: &str, time_field: &str, day: i32) -> Result<String> {
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let value = epoch
        .checked_add_signed(Duration::days(i64::from(day)))
        .ok_or_else(|| CntsError::InvalidData(format!("time value {day} is out of range")))?;
    let result = if field == time_field || field == "day" {
        value.format("%Y-%m-%d").to_string()
    } else {
        match field {
            "year" => format!("{:04}", value.year()),
            "half_year" => {
                if value.month() <= 6 {
                    "上半年".to_string()
                } else {
                    "下半年".to_string()
                }
            }
            "quarter" => format!("{:04}Q{}", value.year(), value.month0() / 3 + 1),
            "season" => match value.month() {
                3..=5 => "春季".to_string(),
                6..=8 => "夏季".to_string(),
                9..=11 => "秋季".to_string(),
                _ => "冬季".to_string(),
            },
            "month" => value.format("%Y%m").to_string(),
            "month_parity" => {
                if value.month() % 2 == 0 {
                    "双月".to_string()
                } else {
                    "单月".to_string()
                }
            }
            "week" => {
                let iso = value.iso_week();
                format!("{:04}W{:02}", iso.year(), iso.week())
            }
            "weekday_type" => {
                if value.weekday().number_from_monday() <= 5 {
                    "工作日".to_string()
                } else {
                    "周末".to_string()
                }
            }
            _ => {
                return Err(CntsError::UnsupportedQuery(format!(
                    "unknown virtual time group {field}"
                )))
            }
        }
    };
    Ok(result)
}

pub fn format_scaled(value: i128, scale: u32) -> String {
    if scale == 0 {
        return value.to_string();
    }
    let negative = value < 0;
    let absolute = value.unsigned_abs();
    let factor = 10_u128.pow(scale);
    let whole = absolute / factor;
    let fraction = absolute % factor;
    format!(
        "{}{}.{:0width$}",
        if negative { "-" } else { "" },
        whole,
        fraction,
        width = scale as usize
    )
}

#[cfg(test)]
mod tests {
    use super::format_scaled;

    #[test]
    fn formats_scaled_values_exactly() {
        assert_eq!(format_scaled(1230, 2), "12.30");
        assert_eq!(format_scaled(-125, 3), "-0.125");
        assert_eq!(format_scaled(10, 0), "10");
    }
}
