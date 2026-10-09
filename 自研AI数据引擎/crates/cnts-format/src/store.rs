use std::{collections::BTreeSet, io::Cursor};

use crate::binary::{
    align_input, align_output, ensure_consumed, put_i128, put_string, put_u32, put_u64, read_i128,
    read_string, read_u32, read_u64,
};
use crate::{CntsError, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MetricColumn {
    pub name: String,
    pub scale: u32,
    pub values: Vec<i128>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreData {
    pub node_count: u64,
    pub metrics: Vec<MetricColumn>,
}

impl StoreData {
    pub fn validate(&self) -> Result<()> {
        let mut names = BTreeSet::new();
        for metric in &self.metrics {
            if metric.name.trim().is_empty() {
                return Err(CntsError::InvalidData(
                    "store metric name is empty".to_string(),
                ));
            }
            if !names.insert(metric.name.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate store metric {}",
                    metric.name
                )));
            }
            if metric.values.len() as u64 != self.node_count {
                return Err(CntsError::InvalidData(format!(
                    "metric {} has {} values, expected {}",
                    metric.name,
                    metric.values.len(),
                    self.node_count
                )));
            }
        }
        Ok(())
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let mut body = Vec::new();
        put_u64(&mut body, self.node_count);
        put_u32(&mut body, self.metrics.len() as u32);
        for metric in &self.metrics {
            put_string(&mut body, &metric.name);
            put_u32(&mut body, metric.scale);
            align_output(&mut body);
            for value in &metric.values {
                put_i128(&mut body, *value);
            }
        }
        Ok(body)
    }

    pub fn decode(body: &[u8]) -> Result<Self> {
        let mut cursor = Cursor::new(body);
        let node_count = read_u64(&mut cursor)?;
        let metric_count = read_u32(&mut cursor)? as usize;
        let mut metrics = Vec::with_capacity(metric_count);
        for _ in 0..metric_count {
            let name = read_string(&mut cursor)?;
            let scale = read_u32(&mut cursor)?;
            align_input(&mut cursor)?;
            let mut values = Vec::with_capacity(node_count as usize);
            for _ in 0..node_count {
                values.push(read_i128(&mut cursor)?);
            }
            metrics.push(MetricColumn {
                name,
                scale,
                values,
            });
        }
        ensure_consumed(&cursor)?;
        let decoded = Self {
            node_count,
            metrics,
        };
        decoded.validate()?;
        Ok(decoded)
    }

    pub fn metric(&self, name: &str) -> Option<&MetricColumn> {
        self.metrics.iter().find(|metric| metric.name == name)
    }
}
