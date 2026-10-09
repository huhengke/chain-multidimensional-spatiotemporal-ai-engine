use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::DatasetSchema;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Superblock {
    pub format: String,
    pub format_major: u16,
    pub format_minor: u16,
    pub generation_id: String,
    pub schema_id: String,
    pub dataset_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct FileEntry {
    pub name: String,
    pub file_type: String,
    pub bytes: u64,
    pub blocks: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GenerationManifest {
    pub generation_id: String,
    pub schema_id: String,
    pub dataset_id: String,
    pub created_at: String,
    pub row_count: u64,
    pub node_count: u64,
    pub schema: DatasetSchema,
    pub files: Vec<FileEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Checksums {
    pub algorithm: String,
    pub files: BTreeMap<String, String>,
}
