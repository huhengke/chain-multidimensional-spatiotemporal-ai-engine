mod input;
mod publish;
mod tree;

use std::path::Path;

use cnts_format::Result;

pub use input::{
    dimension_names, load_records, load_schema, parse_scaled_integer, NormalizedRecord,
};
pub use publish::{rollback, BuildReport};
pub use tree::{build_artifacts, BuildArtifacts};

pub fn build_generation(schema_path: &Path, input_path: &Path, root: &Path) -> Result<BuildReport> {
    let schema = load_schema(schema_path)?;
    let records = load_records(input_path, &schema)?;
    let artifacts = build_artifacts(&schema, records)?;
    publish::write_and_publish(root, &schema, artifacts)
}
