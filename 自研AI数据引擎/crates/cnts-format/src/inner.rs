use std::{collections::HashSet, io::Cursor};

use crate::binary::{
    align_input, align_output, ensure_consumed, put_u16, put_u32, put_u64, read_exact, read_u16,
    read_u32, read_u64,
};
use crate::{CntsError, Result};

pub const NONE_ORDINAL: u64 = u64::MAX;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InnerColumns {
    pub logical_node_id: Vec<[u8; 16]>,
    pub parent_ordinal: Vec<u64>,
    pub first_child_ordinal: Vec<u64>,
    pub next_sibling_ordinal: Vec<u64>,
    pub axis_id: Vec<u32>,
    pub value_id: Vec<u32>,
    pub temporal_layer: Vec<u32>,
    pub branch_depth: Vec<u16>,
    pub flags: Vec<u16>,
    pub aggregate_offset: Vec<u64>,
    pub outer_offset: Vec<u64>,
    pub detail_start: Vec<u64>,
    pub detail_count: Vec<u64>,
}

impl InnerColumns {
    pub fn node_count(&self) -> usize {
        self.logical_node_id.len()
    }

    pub fn validate(&self) -> Result<()> {
        let expected = self.node_count();
        let lengths = [
            ("parent_ordinal", self.parent_ordinal.len()),
            ("first_child_ordinal", self.first_child_ordinal.len()),
            ("next_sibling_ordinal", self.next_sibling_ordinal.len()),
            ("axis_id", self.axis_id.len()),
            ("value_id", self.value_id.len()),
            ("temporal_layer", self.temporal_layer.len()),
            ("branch_depth", self.branch_depth.len()),
            ("flags", self.flags.len()),
            ("aggregate_offset", self.aggregate_offset.len()),
            ("outer_offset", self.outer_offset.len()),
            ("detail_start", self.detail_start.len()),
            ("detail_count", self.detail_count.len()),
        ];
        for (name, actual) in lengths {
            if actual != expected {
                return Err(CntsError::InvalidData(format!(
                    "inner column {name} has {actual} values, expected {expected}"
                )));
            }
        }
        for (index, parent) in self.parent_ordinal.iter().enumerate() {
            for (relation, ordinal) in [
                ("parent", *parent),
                ("first child", self.first_child_ordinal[index]),
                ("next sibling", self.next_sibling_ordinal[index]),
            ] {
                if ordinal != NONE_ORDINAL && ordinal as usize >= expected {
                    return Err(CntsError::InvalidData(format!(
                        "node {index} references invalid {relation} {ordinal}"
                    )));
                }
                if ordinal == index as u64 {
                    return Err(CntsError::InvalidData(format!(
                        "node {index} references itself as {relation}"
                    )));
                }
            }
            self.detail_start[index]
                .checked_add(self.detail_count[index])
                .ok_or_else(|| {
                    CntsError::InvalidData(format!("node {index} detail range overflows u64"))
                })?;
        }
        let unique_ids: HashSet<_> = self.logical_node_id.iter().collect();
        if unique_ids.len() != expected {
            return Err(CntsError::InvalidData(
                "inner topology contains duplicate logical node ids".to_string(),
            ));
        }
        Ok(())
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let mut body = Vec::new();
        put_u64(&mut body, self.node_count() as u64);
        align_output(&mut body);
        for value in &self.logical_node_id {
            body.extend_from_slice(value);
        }
        for values in [
            &self.parent_ordinal,
            &self.first_child_ordinal,
            &self.next_sibling_ordinal,
        ] {
            align_output(&mut body);
            for value in values {
                put_u64(&mut body, *value);
            }
        }
        for values in [&self.axis_id, &self.value_id, &self.temporal_layer] {
            align_output(&mut body);
            for value in values {
                put_u32(&mut body, *value);
            }
        }
        for values in [&self.branch_depth, &self.flags] {
            align_output(&mut body);
            for value in values {
                put_u16(&mut body, *value);
            }
        }
        for values in [
            &self.aggregate_offset,
            &self.outer_offset,
            &self.detail_start,
            &self.detail_count,
        ] {
            align_output(&mut body);
            for value in values {
                put_u64(&mut body, *value);
            }
        }
        Ok(body)
    }

    pub fn decode(body: &[u8]) -> Result<Self> {
        let mut cursor = Cursor::new(body);
        let count = read_u64(&mut cursor)? as usize;
        if count > 1_000_000_000 {
            return Err(CntsError::InvalidData(format!(
                "node count {count} exceeds safety limit"
            )));
        }
        align_input(&mut cursor)?;
        let mut logical_node_id = Vec::with_capacity(count);
        for _ in 0..count {
            logical_node_id.push(read_exact::<16>(&mut cursor)?);
        }
        let read_u64_vec = |cursor: &mut Cursor<&[u8]>| -> Result<Vec<u64>> {
            align_input(cursor)?;
            (0..count).map(|_| read_u64(cursor)).collect()
        };
        let read_u32_vec = |cursor: &mut Cursor<&[u8]>| -> Result<Vec<u32>> {
            align_input(cursor)?;
            (0..count).map(|_| read_u32(cursor)).collect()
        };
        let read_u16_vec = |cursor: &mut Cursor<&[u8]>| -> Result<Vec<u16>> {
            align_input(cursor)?;
            (0..count).map(|_| read_u16(cursor)).collect()
        };
        let decoded = Self {
            logical_node_id,
            parent_ordinal: read_u64_vec(&mut cursor)?,
            first_child_ordinal: read_u64_vec(&mut cursor)?,
            next_sibling_ordinal: read_u64_vec(&mut cursor)?,
            axis_id: read_u32_vec(&mut cursor)?,
            value_id: read_u32_vec(&mut cursor)?,
            temporal_layer: read_u32_vec(&mut cursor)?,
            branch_depth: read_u16_vec(&mut cursor)?,
            flags: read_u16_vec(&mut cursor)?,
            aggregate_offset: read_u64_vec(&mut cursor)?,
            outer_offset: read_u64_vec(&mut cursor)?,
            detail_start: read_u64_vec(&mut cursor)?,
            detail_count: read_u64_vec(&mut cursor)?,
        };
        ensure_consumed(&cursor)?;
        decoded.validate()?;
        Ok(decoded)
    }
}
