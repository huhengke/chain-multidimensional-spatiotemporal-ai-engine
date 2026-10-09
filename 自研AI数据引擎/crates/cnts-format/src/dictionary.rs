use std::{collections::BTreeSet, io::Cursor};

use crate::binary::{ensure_consumed, put_string, put_u32, read_string, read_u32};
use crate::{CntsError, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FieldDictionary {
    pub field: String,
    pub values: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DictionarySet {
    pub fields: Vec<FieldDictionary>,
}

impl DictionarySet {
    pub fn validate(&self) -> Result<()> {
        let mut field_names = BTreeSet::new();
        for dictionary in &self.fields {
            if dictionary.field.trim().is_empty() {
                return Err(CntsError::InvalidData(
                    "dictionary field name is empty".to_string(),
                ));
            }
            if !field_names.insert(dictionary.field.as_str()) {
                return Err(CntsError::InvalidData(format!(
                    "duplicate dictionary field {}",
                    dictionary.field
                )));
            }
            let unique_values: BTreeSet<_> = dictionary.values.iter().collect();
            if unique_values.len() != dictionary.values.len() {
                return Err(CntsError::InvalidData(format!(
                    "dictionary {} contains duplicate values",
                    dictionary.field
                )));
            }
        }
        Ok(())
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let mut body = Vec::new();
        put_u32(&mut body, self.fields.len() as u32);
        for dictionary in &self.fields {
            put_string(&mut body, &dictionary.field);
            put_u32(&mut body, dictionary.values.len() as u32);
            for value in &dictionary.values {
                put_string(&mut body, value);
            }
        }
        Ok(body)
    }

    pub fn decode(body: &[u8]) -> Result<Self> {
        let mut cursor = Cursor::new(body);
        let field_count = read_u32(&mut cursor)? as usize;
        if field_count > 100_000 {
            return Err(CntsError::InvalidData(format!(
                "dictionary field count {field_count} exceeds limit"
            )));
        }
        let mut fields = Vec::with_capacity(field_count);
        for _ in 0..field_count {
            let field = read_string(&mut cursor)?;
            let value_count = read_u32(&mut cursor)? as usize;
            if value_count > 100_000_000 {
                return Err(CntsError::InvalidData(format!(
                    "dictionary value count {value_count} exceeds limit"
                )));
            }
            let mut values = Vec::with_capacity(value_count);
            for _ in 0..value_count {
                values.push(read_string(&mut cursor)?);
            }
            fields.push(FieldDictionary { field, values });
        }
        ensure_consumed(&cursor)?;
        let decoded = Self { fields };
        decoded.validate()?;
        Ok(decoded)
    }

    pub fn field(&self, name: &str) -> Option<&FieldDictionary> {
        self.fields.iter().find(|field| field.field == name)
    }
}
