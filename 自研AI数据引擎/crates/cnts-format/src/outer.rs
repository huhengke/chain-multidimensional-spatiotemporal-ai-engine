use std::{
    collections::BTreeSet,
    io::{Cursor, Read},
};

use roaring::RoaringBitmap;

use crate::binary::{
    ensure_consumed, put_bytes, put_string, put_u32, put_u8, read_bytes, read_string, read_u32,
    read_u8,
};
use crate::{CntsError, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum PostingEncoding {
    DeltaVarint = 1,
    Roaring = 2,
}

impl TryFrom<u8> for PostingEncoding {
    type Error = CntsError;

    fn try_from(value: u8) -> Result<Self> {
        match value {
            1 => Ok(Self::DeltaVarint),
            2 => Ok(Self::Roaring),
            _ => Err(CntsError::InvalidData(format!(
                "unknown posting encoding {value}"
            ))),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PostingList {
    pub value_id: u32,
    pub rows: Vec<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OuterField {
    pub name: String,
    pub postings: Vec<PostingList>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OuterIndex {
    pub fields: Vec<OuterField>,
}

fn put_varint(target: &mut Vec<u8>, mut value: u32) {
    while value >= 0x80 {
        target.push((value as u8) | 0x80);
        value >>= 7;
    }
    target.push(value as u8);
}

fn read_varint(cursor: &mut Cursor<&[u8]>) -> Result<u32> {
    let mut value = 0_u32;
    for shift in (0..35).step_by(7) {
        let mut byte = [0_u8; 1];
        cursor.read_exact(&mut byte)?;
        value |= ((byte[0] & 0x7f) as u32) << shift;
        if byte[0] & 0x80 == 0 {
            return Ok(value);
        }
    }
    Err(CntsError::InvalidData("varint exceeds u32".to_string()))
}

fn encode_delta(rows: &[u32]) -> Vec<u8> {
    let mut encoded = Vec::new();
    let mut previous = 0_u32;
    for (index, row) in rows.iter().enumerate() {
        let delta = if index == 0 { *row } else { *row - previous };
        put_varint(&mut encoded, delta);
        previous = *row;
    }
    encoded
}

fn decode_delta(bytes: &[u8], count: usize) -> Result<Vec<u32>> {
    let mut cursor = Cursor::new(bytes);
    let mut rows = Vec::with_capacity(count);
    let mut previous = 0_u32;
    for index in 0..count {
        let delta = read_varint(&mut cursor)?;
        let row = if index == 0 {
            delta
        } else {
            previous
                .checked_add(delta)
                .ok_or_else(|| CntsError::InvalidData("posting delta overflow".to_string()))?
        };
        rows.push(row);
        previous = row;
    }
    ensure_consumed(&cursor)?;
    Ok(rows)
}

impl OuterIndex {
    pub fn validate(&self) -> Result<()> {
        let mut field_names = BTreeSet::new();
        for field in &self.fields {
            if field.name.trim().is_empty() {
                return Err(CntsError::InvalidData(
                    "outer index field name is empty".to_string(),
                ));
            }
            if !field_names.insert(field.name.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate outer index field {}",
                    field.name
                )));
            }
            let mut value_ids = BTreeSet::new();
            for posting in &field.postings {
                if !value_ids.insert(posting.value_id) {
                    return Err(CntsError::InvalidData(format!(
                        "outer index {} has duplicate value id {}",
                        field.name, posting.value_id
                    )));
                }
                if posting.rows.windows(2).any(|pair| pair[0] >= pair[1]) {
                    return Err(CntsError::InvalidData(format!(
                        "outer index {} posting {} is not strictly increasing",
                        field.name, posting.value_id
                    )));
                }
            }
        }
        Ok(())
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let mut body = Vec::new();
        put_u32(&mut body, self.fields.len() as u32);
        for field in &self.fields {
            put_string(&mut body, &field.name);
            put_u32(&mut body, field.postings.len() as u32);
            for posting in &field.postings {
                let mut rows = posting.rows.clone();
                rows.sort_unstable();
                rows.dedup();
                let delta = encode_delta(&rows);
                let bitmap: RoaringBitmap = rows.iter().copied().collect();
                let mut roaring = Vec::new();
                bitmap.serialize_into(&mut roaring)?;
                let (encoding, payload) = if roaring.len() < delta.len() {
                    (PostingEncoding::Roaring, roaring)
                } else {
                    (PostingEncoding::DeltaVarint, delta)
                };
                put_u32(&mut body, posting.value_id);
                put_u32(&mut body, rows.len() as u32);
                put_u8(&mut body, encoding as u8);
                put_bytes(&mut body, &payload);
            }
        }
        Ok(body)
    }

    pub fn decode(body: &[u8]) -> Result<Self> {
        let mut cursor = Cursor::new(body);
        let field_count = read_u32(&mut cursor)? as usize;
        let mut fields = Vec::with_capacity(field_count);
        for _ in 0..field_count {
            let name = read_string(&mut cursor)?;
            let posting_count = read_u32(&mut cursor)? as usize;
            let mut postings = Vec::with_capacity(posting_count);
            for _ in 0..posting_count {
                let value_id = read_u32(&mut cursor)?;
                let row_count = read_u32(&mut cursor)? as usize;
                let encoding = PostingEncoding::try_from(read_u8(&mut cursor)?)?;
                let payload = read_bytes(&mut cursor)?;
                let rows = match encoding {
                    PostingEncoding::DeltaVarint => decode_delta(&payload, row_count)?,
                    PostingEncoding::Roaring => {
                        let bitmap = RoaringBitmap::deserialize_from(Cursor::new(payload))?;
                        let rows: Vec<u32> = bitmap.iter().collect();
                        if rows.len() != row_count {
                            return Err(CntsError::InvalidData(format!(
                                "roaring posting has {} rows, expected {row_count}",
                                rows.len()
                            )));
                        }
                        rows
                    }
                };
                postings.push(PostingList { value_id, rows });
            }
            fields.push(OuterField { name, postings });
        }
        ensure_consumed(&cursor)?;
        let decoded = Self { fields };
        decoded.validate()?;
        Ok(decoded)
    }

    pub fn field(&self, name: &str) -> Option<&OuterField> {
        self.fields.iter().find(|field| field.name == name)
    }
}
