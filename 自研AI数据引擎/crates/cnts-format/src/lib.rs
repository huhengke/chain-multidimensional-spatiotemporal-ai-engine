mod binary;
mod details;
mod dictionary;
mod error;
mod file;
mod generation;
mod header;
mod inner;
mod manifest;
mod outer;
mod schema;
mod store;

pub use details::{ColumnKind, ColumnValues, DetailColumn, DetailData, DETAIL_BLOCK_ROWS};
pub use dictionary::{DictionarySet, FieldDictionary};
pub use error::{CntsError, Result};
pub use file::{hash_file, read_json_file, write_cnts_file, write_json_file, MappedCntsFile};
pub use generation::{read_current_generation, verify_generation};
pub use header::{FileHeader, FileType, FORMAT_MAJOR, FORMAT_MINOR, HEADER_SIZE};
pub use inner::{InnerColumns, NONE_ORDINAL};
pub use manifest::{Checksums, FileEntry, GenerationManifest, Superblock};
pub use outer::{OuterField, OuterIndex, PostingEncoding, PostingList};
pub use schema::{
    DatasetSchema, DetailPolicy, FilterOp, MergeMode, MetricSchema, QueryFilter, QueryPlan,
    QueryResult, QueryResultRow, RouteStep, TimeRange,
};
pub use store::{MetricColumn, StoreData};

pub fn stable_logical_node_id(
    dataset_id: &str,
    schema_major: u32,
    time_path: &str,
    dimension_path: &str,
) -> [u8; 16] {
    let canonical = format!(
        "{}\u{1f}{}\u{1f}{}\u{1f}{}",
        dataset_id.trim().to_lowercase(),
        schema_major,
        time_path.trim().to_lowercase(),
        dimension_path.trim().to_lowercase()
    );
    let digest = blake3::hash(canonical.as_bytes());
    let mut id = [0_u8; 16];
    id.copy_from_slice(&digest.as_bytes()[..16]);
    id
}

pub fn schema_id(schema: &DatasetSchema) -> Result<[u8; 16]> {
    let encoded = serde_json::to_vec(schema)?;
    let digest = blake3::hash(&encoded);
    let mut id = [0_u8; 16];
    id.copy_from_slice(&digest.as_bytes()[..16]);
    Ok(id)
}
