use std::{collections::BTreeSet, io::Cursor};

use crate::binary::{
    ensure_consumed, put_bytes, put_i32, put_i64, put_string, put_u32, put_u64, put_u8, read_bytes,
    read_i32, read_i64, read_string, read_u32, read_u64, read_u8,
};
use crate::{CntsError, Result};

pub const DETAIL_BLOCK_ROWS: usize = 65_536;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum ColumnKind {
    Time = 1,
    Dictionary = 2,
    Metric = 3,
}

impl TryFrom<u8> for ColumnKind {
    type Error = CntsError;

    fn try_from(value: u8) -> Result<Self> {
        match value {
            1 => Ok(Self::Time),
            2 => Ok(Self::Dictionary),
            3 => Ok(Self::Metric),
            _ => Err(CntsError::InvalidData(format!(
                "unknown detail column kind {value}"
            ))),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ColumnValues {
    I32(Vec<i32>),
    U32(Vec<u32>),
    I64(Vec<i64>),
}

impl ColumnValues {
    fn len(&self) -> usize {
        match self {
            Self::I32(values) => values.len(),
            Self::U32(values) => values.len(),
            Self::I64(values) => values.len(),
        }
    }

    fn raw_range(&self, start: usize, end: usize) -> Vec<u8> {
        let mut raw = Vec::new();
        match self {
            Self::I32(values) => values[start..end]
                .iter()
                .for_each(|value| put_i32(&mut raw, *value)),
            Self::U32(values) => values[start..end]
                .iter()
                .for_each(|value| put_u32(&mut raw, *value)),
            Self::I64(values) => values[start..end]
                .iter()
                .for_each(|value| put_i64(&mut raw, *value)),
        }
        raw
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetailColumn {
    pub name: String,
    pub kind: ColumnKind,
    pub scale: u32,
    pub null_bitmap: Vec<u8>,
    pub values: ColumnValues,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetailData {
    pub row_count: u64,
    pub columns: Vec<DetailColumn>,
}

impl DetailData {
    pub fn block_count(&self) -> u64 {
        self.row_count.div_ceil(DETAIL_BLOCK_ROWS as u64)
    }

    pub fn validate(&self) -> Result<()> {
        let expected_null_bytes = (self.row_count as usize).div_ceil(8);
        let mut names = BTreeSet::new();
        for column in &self.columns {
            if column.name.trim().is_empty() {
                return Err(CntsError::InvalidData(
                    "detail column name is empty".to_string(),
                ));
            }
            if !names.insert(column.name.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate detail column {}",
                    column.name
                )));
            }
            if column.values.len() as u64 != self.row_count {
                return Err(CntsError::InvalidData(format!(
                    "detail column {} has {} rows, expected {}",
                    column.name,
                    column.values.len(),
                    self.row_count
                )));
            }
            if column.null_bitmap.len() != expected_null_bytes {
                return Err(CntsError::InvalidData(format!(
                    "detail column {} has {} null bytes, expected {expected_null_bytes}",
                    column.name,
                    column.null_bitmap.len()
                )));
            }
            let kind_matches = matches!(
                (column.kind, &column.values),
                (ColumnKind::Time, ColumnValues::I32(_))
                    | (ColumnKind::Dictionary, ColumnValues::U32(_))
                    | (ColumnKind::Metric, ColumnValues::I64(_))
            );
            if !kind_matches {
                return Err(CntsError::InvalidData(format!(
                    "detail column {} kind/value mismatch",
                    column.name
                )));
            }
        }
        Ok(())
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let mut body = Vec::new();
        put_u64(&mut body, self.row_count);
        put_u32(&mut body, self.columns.len() as u32);
        for column in &self.columns {
            put_string(&mut body, &column.name);
            put_u8(&mut body, column.kind as u8);
            put_u32(&mut body, column.scale);
            put_bytes(&mut body, &column.null_bitmap);
            put_u32(&mut body, self.block_count() as u32);
            for start in (0..self.row_count as usize).step_by(DETAIL_BLOCK_ROWS) {
                let end = (start + DETAIL_BLOCK_ROWS).min(self.row_count as usize);
                let raw = column.values.raw_range(start, end);
                let compressed = zstandard::encode_all(&raw).map_err(|error| {
                    CntsError::InvalidData(format!("Zstandard encode failed: {error}"))
                })?;
                put_u32(&mut body, (end - start) as u32);
                put_u64(&mut body, raw.len() as u64);
                put_u32(&mut body, crc32c::crc32c(&raw));
                put_bytes(&mut body, &compressed);
            }
        }
        Ok(body)
    }

    pub fn decode(body: &[u8]) -> Result<Self> {
        let mut cursor = Cursor::new(body);
        let row_count = read_u64(&mut cursor)?;
        let column_count = read_u32(&mut cursor)? as usize;
        let mut columns = Vec::with_capacity(column_count);
        for _ in 0..column_count {
            let name = read_string(&mut cursor)?;
            let kind = ColumnKind::try_from(read_u8(&mut cursor)?)?;
            let scale = read_u32(&mut cursor)?;
            let null_bitmap = read_bytes(&mut cursor)?;
            let block_count = read_u32(&mut cursor)? as usize;
            let expected_blocks = (row_count as usize).div_ceil(DETAIL_BLOCK_ROWS);
            if block_count != expected_blocks {
                return Err(CntsError::InvalidData(format!(
                    "detail column {name} has {block_count} blocks, expected {expected_blocks}"
                )));
            }
            let mut values = match kind {
                ColumnKind::Time => ColumnValues::I32(Vec::with_capacity(row_count as usize)),
                ColumnKind::Dictionary => ColumnValues::U32(Vec::with_capacity(row_count as usize)),
                ColumnKind::Metric => ColumnValues::I64(Vec::with_capacity(row_count as usize)),
            };
            let mut decoded_rows = 0_usize;
            for block_index in 0..block_count {
                let block_rows = read_u32(&mut cursor)? as usize;
                let expected_rows = (row_count as usize - decoded_rows).min(DETAIL_BLOCK_ROWS);
                if block_rows != expected_rows {
                    return Err(CntsError::InvalidData(format!(
                        "detail column {name} block {block_index} has {block_rows} rows, expected {expected_rows}"
                    )));
                }
                let raw_len = read_u64(&mut cursor)? as usize;
                let expected_width = match kind {
                    ColumnKind::Time | ColumnKind::Dictionary => 4,
                    ColumnKind::Metric => 8,
                };
                let expected_raw_len = block_rows.checked_mul(expected_width).ok_or_else(|| {
                    CntsError::InvalidData(format!(
                        "detail column {name} block {block_index} length overflows"
                    ))
                })?;
                if raw_len != expected_raw_len {
                    return Err(CntsError::InvalidData(format!(
                        "detail column {name} block {block_index} raw length is {raw_len}, expected {expected_raw_len}"
                    )));
                }
                let expected_crc = read_u32(&mut cursor)?;
                let compressed = read_bytes(&mut cursor)?;
                let raw = zstandard::decode_all(&compressed).map_err(|error| {
                    CntsError::InvalidData(format!("Zstandard decode failed: {error}"))
                })?;
                if raw.len() != raw_len {
                    return Err(CntsError::InvalidData(format!(
                        "detail column {name} block {block_index} decoded to {} bytes, expected {raw_len}",
                        raw.len()
                    )));
                }
                let actual_crc = crc32c::crc32c(&raw);
                if actual_crc != expected_crc {
                    return Err(CntsError::InvalidData(format!(
                        "detail column {name} block {block_index} CRC32C mismatch"
                    )));
                }
                let mut raw_cursor = Cursor::new(raw.as_slice());
                match &mut values {
                    ColumnValues::I32(output) => output.extend(
                        (0..block_rows)
                            .map(|_| read_i32(&mut raw_cursor))
                            .collect::<Result<Vec<_>>>()?,
                    ),
                    ColumnValues::U32(output) => output.extend(
                        (0..block_rows)
                            .map(|_| read_u32(&mut raw_cursor))
                            .collect::<Result<Vec<_>>>()?,
                    ),
                    ColumnValues::I64(output) => output.extend(
                        (0..block_rows)
                            .map(|_| read_i64(&mut raw_cursor))
                            .collect::<Result<Vec<_>>>()?,
                    ),
                }
                ensure_consumed(&raw_cursor)?;
                decoded_rows += block_rows;
            }
            columns.push(DetailColumn {
                name,
                kind,
                scale,
                null_bitmap,
                values,
            });
        }
        ensure_consumed(&cursor)?;
        let decoded = Self { row_count, columns };
        decoded.validate()?;
        Ok(decoded)
    }

    pub fn column(&self, name: &str) -> Option<&DetailColumn> {
        self.columns.iter().find(|column| column.name == name)
    }
}
