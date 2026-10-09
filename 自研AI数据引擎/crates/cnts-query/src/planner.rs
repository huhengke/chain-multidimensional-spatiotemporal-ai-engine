use std::collections::BTreeSet;

use chrono::NaiveDate;
use cnts_format::{CntsError, DatasetSchema, FilterOp, MergeMode, QueryPlan, Result};

const VIRTUAL_TIME_GROUPS: &[&str] = &[
    "year",
    "half_year",
    "quarter",
    "season",
    "month",
    "month_parity",
    "week",
    "weekday_type",
    "day",
];

pub struct ValidatedPlan {
    pub start_day: i32,
    pub end_day: i32,
}

pub fn validate_plan(plan: &QueryPlan, schema: &DatasetSchema) -> Result<ValidatedPlan> {
    if plan.plan_version != 1 {
        return Err(CntsError::UnsupportedQuery(format!(
            "plan_version {} is unsupported",
            plan.plan_version
        )));
    }
    if plan.datasets.len() != 1 || plan.datasets[0] != schema.dataset_id {
        return Err(CntsError::UnsupportedQuery(format!(
            "phase 0 accepts exactly dataset {}",
            schema.dataset_id
        )));
    }
    if !plan.comparisons.is_empty() {
        return Err(CntsError::UnsupportedQuery(
            "comparisons enter in phase 1".to_string(),
        ));
    }
    if plan.merge_mode != MergeMode::Separate {
        return Err(CntsError::UnsupportedQuery(
            "phase 0 supports only merge_mode=separate".to_string(),
        ));
    }
    let metric_names: BTreeSet<&str> = schema
        .metrics
        .iter()
        .map(|metric| metric.name.as_str())
        .collect();
    for metric in &plan.metrics {
        if !metric_names.contains(metric.as_str()) {
            return Err(CntsError::UnsupportedQuery(format!(
                "unknown metric {metric}"
            )));
        }
    }
    if plan.metrics.is_empty() {
        return Err(CntsError::UnsupportedQuery(
            "at least one metric is required".to_string(),
        ));
    }
    let dimension_names: BTreeSet<&str> = schema
        .common_dimensions
        .iter()
        .chain(schema.outer_dimensions.iter())
        .map(String::as_str)
        .collect();
    let mut grouped_fields = BTreeSet::new();
    for field in &plan.group_by {
        let is_time_group =
            field == &schema.time_field || VIRTUAL_TIME_GROUPS.contains(&field.as_str());
        if !dimension_names.contains(field.as_str()) && !is_time_group {
            return Err(CntsError::UnsupportedQuery(format!(
                "group_by field {field} is not a declared dimension or time group"
            )));
        }
        if !grouped_fields.insert(field.as_str()) {
            return Err(CntsError::UnsupportedQuery(format!(
                "group_by field {field} is duplicated"
            )));
        }
    }
    let mut filtered_fields = BTreeSet::new();
    for filter in &plan.filters {
        if !dimension_names.contains(filter.field.as_str()) {
            return Err(CntsError::UnsupportedQuery(format!(
                "filter field {} is not a declared dimension",
                filter.field
            )));
        }
        if !filtered_fields.insert(filter.field.as_str()) {
            return Err(CntsError::UnsupportedQuery(format!(
                "filter field {} is duplicated",
                filter.field
            )));
        }
        match filter.op {
            FilterOp::Eq if !filter.value.is_string() => {
                return Err(CntsError::UnsupportedQuery(format!(
                    "eq filter {} requires a string value",
                    filter.field
                )))
            }
            FilterOp::In if !filter.value.is_array() => {
                return Err(CntsError::UnsupportedQuery(format!(
                    "in filter {} requires an array value",
                    filter.field
                )))
            }
            FilterOp::In
                if filter
                    .value
                    .as_array()
                    .is_some_and(|values| values.iter().any(|value| !value.is_string())) =>
            {
                return Err(CntsError::UnsupportedQuery(format!(
                    "in filter {} requires only string values",
                    filter.field
                )))
            }
            _ => {}
        }
    }
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let start = NaiveDate::parse_from_str(&plan.time_range.start, "%Y-%m-%d")
        .map_err(|error| CntsError::UnsupportedQuery(format!("invalid start date: {error}")))?;
    let end = NaiveDate::parse_from_str(&plan.time_range.end, "%Y-%m-%d")
        .map_err(|error| CntsError::UnsupportedQuery(format!("invalid end date: {error}")))?;
    if start > end {
        return Err(CntsError::UnsupportedQuery(
            "time range start is after end".to_string(),
        ));
    }
    let start_day = i32::try_from(start.signed_duration_since(epoch).num_days())
        .map_err(|_| CntsError::UnsupportedQuery("start date is out of range".to_string()))?;
    let end_day = i32::try_from(end.signed_duration_since(epoch).num_days())
        .map_err(|_| CntsError::UnsupportedQuery("end date is out of range".to_string()))?;
    Ok(ValidatedPlan { start_day, end_day })
}
