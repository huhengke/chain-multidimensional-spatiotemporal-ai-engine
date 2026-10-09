use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{CntsError, Result};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MetricSchema {
    pub name: String,
    pub scale: u32,
    #[serde(default)]
    pub unit: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DatasetSchema {
    pub dataset_id: String,
    pub schema_major: u32,
    pub time_field: String,
    #[serde(default = "default_time_format")]
    pub time_format: String,
    #[serde(default)]
    pub common_dimensions: Vec<String>,
    #[serde(default)]
    pub outer_dimensions: Vec<String>,
    #[serde(default)]
    pub growth_dimensions: Vec<String>,
    #[serde(default = "default_growth_threshold")]
    pub growth_threshold: u64,
    pub metrics: Vec<MetricSchema>,
}

fn default_time_format() -> String {
    "%Y-%m-%d".to_string()
}

fn default_growth_threshold() -> u64 {
    10_000
}

impl DatasetSchema {
    pub fn validate(&self) -> Result<()> {
        if self.dataset_id.trim().is_empty() {
            return Err(CntsError::InvalidData("dataset_id is empty".to_string()));
        }
        if self.schema_major == 0 {
            return Err(CntsError::InvalidData(
                "schema_major must be greater than zero".to_string(),
            ));
        }
        if self.time_field.trim().is_empty() {
            return Err(CntsError::InvalidData("time_field is empty".to_string()));
        }
        if self.time_format.trim().is_empty() {
            return Err(CntsError::InvalidData("time_format is empty".to_string()));
        }
        if self.growth_threshold == 0 {
            return Err(CntsError::InvalidData(
                "growth_threshold must be greater than zero".to_string(),
            ));
        }
        if self.metrics.is_empty() {
            return Err(CntsError::InvalidData(
                "at least one metric is required".to_string(),
            ));
        }

        let mut names = BTreeSet::from([self.time_field.as_str()]);
        let mut common_names = BTreeSet::new();
        for dimension in &self.common_dimensions {
            if dimension.trim().is_empty() {
                return Err(CntsError::InvalidData(
                    "common dimension name is empty".to_string(),
                ));
            }
            if !common_names.insert(dimension.as_str()) || !names.insert(dimension.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate or conflicting field {dimension}"
                )));
            }
        }
        let mut outer_names = BTreeSet::new();
        for dimension in &self.outer_dimensions {
            if dimension.trim().is_empty() {
                return Err(CntsError::InvalidData(
                    "outer dimension name is empty".to_string(),
                ));
            }
            if !outer_names.insert(dimension.as_str()) || !names.insert(dimension.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate or conflicting field {dimension}"
                )));
            }
        }
        let mut growth_names = BTreeSet::new();
        for dimension in &self.growth_dimensions {
            if !growth_names.insert(dimension.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate growth dimension {dimension}"
                )));
            }
            if !common_names.contains(dimension.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "growth dimension {dimension} must be a declared common dimension"
                )));
            }
        }
        for metric in &self.metrics {
            if metric.name.trim().is_empty() {
                return Err(CntsError::InvalidData("metric name is empty".to_string()));
            }
            if metric.name == "__record_count" {
                return Err(CntsError::InvalidData(
                    "metric name __record_count is reserved".to_string(),
                ));
            }
            if metric.scale > 12 {
                return Err(CntsError::InvalidData(format!(
                    "metric {} scale {} exceeds limit 12",
                    metric.name, metric.scale
                )));
            }
            if !names.insert(metric.name.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate or conflicting metric {}",
                    metric.name
                )));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FilterOp {
    Eq,
    In,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct QueryFilter {
    pub field: String,
    #[serde(default = "default_filter_op")]
    pub op: FilterOp,
    pub value: Value,
}

fn default_filter_op() -> FilterOp {
    FilterOp::Eq
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TimeRange {
    pub start: String,
    pub end: String,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MergeMode {
    SumTotal,
    #[default]
    Separate,
    TotalAndBreakdown,
    SkipEmpty,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DetailPolicy {
    #[default]
    AllowFallback,
    AggregatesOnly,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct QueryPlan {
    #[serde(default = "default_plan_version")]
    pub plan_version: u32,
    pub datasets: Vec<String>,
    pub time_range: TimeRange,
    #[serde(default)]
    pub filters: Vec<QueryFilter>,
    #[serde(default)]
    pub group_by: Vec<String>,
    pub metrics: Vec<String>,
    #[serde(default)]
    pub comparisons: Vec<String>,
    #[serde(default)]
    pub merge_mode: MergeMode,
    #[serde(default)]
    pub detail_policy: DetailPolicy,
}

fn default_plan_version() -> u32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RouteStep {
    pub operation: String,
    pub detail: String,
    pub matched: u64,
    pub elapsed_micros: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct QueryResultRow {
    pub dimensions: BTreeMap<String, String>,
    pub metrics: BTreeMap<String, String>,
    pub record_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct QueryResult {
    pub dataset_id: String,
    pub generation_id: String,
    pub schema_id: String,
    pub rows: Vec<QueryResultRow>,
    pub route: Vec<RouteStep>,
    pub detail_fallback: bool,
}
