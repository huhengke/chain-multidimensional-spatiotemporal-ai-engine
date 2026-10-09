use std::{
    collections::BTreeMap,
    fs::File,
    io::{BufRead, BufReader},
    path::Path,
};

use chrono::NaiveDate;
use cnts_format::{CntsError, DatasetSchema, Result};
use serde_json::Value;

#[derive(Debug, Clone)]
pub struct NormalizedRecord {
    pub day: i32,
    pub dimensions: BTreeMap<String, Option<String>>,
    pub metrics: BTreeMap<String, i64>,
}

pub fn load_schema(path: &Path) -> Result<DatasetSchema> {
    let schema: DatasetSchema = serde_json::from_reader(File::open(path)?)?;
    validate_schema(&schema)?;
    Ok(schema)
}

pub fn validate_schema(schema: &DatasetSchema) -> Result<()> {
    schema.validate()
}

pub fn load_records(path: &Path, schema: &DatasetSchema) -> Result<Vec<NormalizedRecord>> {
    let reader = BufReader::new(File::open(path)?);
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let dimensions = dimension_names(schema);
    let mut records = Vec::new();
    for (line_index, line) in reader.lines().enumerate() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(&line).map_err(|error| {
            CntsError::InvalidData(format!("line {} is invalid JSON: {error}", line_index + 1))
        })?;
        let object = value.as_object().ok_or_else(|| {
            CntsError::InvalidData(format!("line {} is not a JSON object", line_index + 1))
        })?;
        let raw_time = object
            .get(&schema.time_field)
            .and_then(Value::as_str)
            .ok_or_else(|| {
                CntsError::InvalidData(format!(
                    "line {} has no string time field {}",
                    line_index + 1,
                    schema.time_field
                ))
            })?;
        let date = NaiveDate::parse_from_str(raw_time, &schema.time_format).map_err(|error| {
            CntsError::InvalidData(format!(
                "line {} time value {raw_time:?} is invalid: {error}",
                line_index + 1
            ))
        })?;
        let day_i64 = date.signed_duration_since(epoch).num_days();
        let day = i32::try_from(day_i64).map_err(|_| {
            CntsError::InvalidData(format!("line {} date is out of range", line_index + 1))
        })?;
        let mut normalized_dimensions = BTreeMap::new();
        for name in &dimensions {
            let normalized = match object.get(name) {
                None | Some(Value::Null) => None,
                Some(Value::String(value)) => {
                    let value = value.trim();
                    (!value.is_empty()).then(|| value.to_string())
                }
                Some(value) => Some(value.to_string()),
            };
            normalized_dimensions.insert(name.clone(), normalized);
        }
        for name in &schema.common_dimensions {
            if normalized_dimensions[name].is_none() {
                return Err(CntsError::InvalidData(format!(
                    "line {} common dimension {} is null or empty",
                    line_index + 1,
                    name
                )));
            }
        }
        let mut metrics = BTreeMap::new();
        for metric in &schema.metrics {
            let raw = object.get(&metric.name).ok_or_else(|| {
                CntsError::InvalidData(format!(
                    "line {} has no metric {}",
                    line_index + 1,
                    metric.name
                ))
            })?;
            let raw_text = match raw {
                Value::String(value) => value.clone(),
                Value::Number(value) => value.to_string(),
                _ => {
                    return Err(CntsError::InvalidData(format!(
                        "line {} metric {} is not numeric",
                        line_index + 1,
                        metric.name
                    )))
                }
            };
            metrics.insert(
                metric.name.clone(),
                parse_scaled_integer(&raw_text, metric.scale).map_err(|error| {
                    CntsError::InvalidData(format!(
                        "line {} metric {}: {error}",
                        line_index + 1,
                        metric.name
                    ))
                })?,
            );
        }
        records.push(NormalizedRecord {
            day,
            dimensions: normalized_dimensions,
            metrics,
        });
    }
    if records.is_empty() {
        return Err(CntsError::InvalidData(
            "input contains no records".to_string(),
        ));
    }
    Ok(records)
}

pub fn dimension_names(schema: &DatasetSchema) -> Vec<String> {
    let mut names = Vec::new();
    for name in schema
        .common_dimensions
        .iter()
        .chain(schema.outer_dimensions.iter())
        .chain(schema.growth_dimensions.iter())
    {
        if !names.contains(name) {
            names.push(name.clone());
        }
    }
    names
}

pub fn parse_scaled_integer(raw: &str, scale: u32) -> std::result::Result<i64, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err("value is empty".to_string());
    }
    let (negative, unsigned) = match raw.as_bytes()[0] {
        b'-' => (true, &raw[1..]),
        b'+' => (false, &raw[1..]),
        _ => (false, raw),
    };
    let mut parts = unsigned.split('.');
    let whole = parts.next().unwrap_or_default();
    let fraction = parts.next().unwrap_or_default();
    if parts.next().is_some()
        || whole.is_empty()
        || !whole.bytes().all(|byte| byte.is_ascii_digit())
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(format!("{raw:?} is not a decimal number"));
    }
    if fraction.len() > scale as usize {
        return Err(format!(
            "{raw:?} has {} decimal places, schema allows {scale}",
            fraction.len()
        ));
    }
    let factor = 10_i128.pow(scale);
    let whole_value = whole
        .parse::<i128>()
        .map_err(|error| format!("invalid integer part: {error}"))?;
    let mut fraction_text = fraction.to_string();
    fraction_text.extend(std::iter::repeat_n('0', scale as usize - fraction.len()));
    let fraction_value = if fraction_text.is_empty() {
        0_i128
    } else {
        fraction_text
            .parse::<i128>()
            .map_err(|error| format!("invalid fractional part: {error}"))?
    };
    let unsigned_value = whole_value
        .checked_mul(factor)
        .and_then(|value| value.checked_add(fraction_value))
        .ok_or_else(|| "scaled decimal overflow".to_string())?;
    let signed = if negative {
        unsigned_value
            .checked_neg()
            .ok_or_else(|| "scaled decimal overflow".to_string())?
    } else {
        unsigned_value
    };
    i64::try_from(signed).map_err(|_| "scaled decimal exceeds i64".to_string())
}

#[cfg(test)]
mod tests {
    use super::{parse_scaled_integer, validate_schema};
    use cnts_format::{DatasetSchema, MetricSchema};

    #[test]
    fn parses_scaled_decimal_exactly() {
        assert_eq!(parse_scaled_integer("12.3", 2).unwrap(), 1230);
        assert_eq!(parse_scaled_integer("-0.125", 3).unwrap(), -125);
        assert!(parse_scaled_integer("1.234", 2).is_err());
    }

    fn valid_schema() -> DatasetSchema {
        DatasetSchema {
            dataset_id: "sales".to_string(),
            schema_major: 1,
            time_field: "date".to_string(),
            time_format: "%Y-%m-%d".to_string(),
            common_dimensions: vec!["company".to_string()],
            outer_dimensions: vec!["category".to_string()],
            growth_dimensions: vec!["company".to_string()],
            growth_threshold: 1_000,
            metrics: vec![MetricSchema {
                name: "amount".to_string(),
                scale: 2,
                unit: "CNY".to_string(),
            }],
        }
    }

    #[test]
    fn rejects_ambiguous_schema_roles() {
        let mut schema = valid_schema();
        schema.growth_dimensions = vec!["category".to_string()];
        assert!(validate_schema(&schema).is_err());

        let mut schema = valid_schema();
        schema.outer_dimensions.push("company".to_string());
        assert!(validate_schema(&schema).is_err());

        let mut schema = valid_schema();
        schema.growth_threshold = 0;
        assert!(validate_schema(&schema).is_err());
    }
}
