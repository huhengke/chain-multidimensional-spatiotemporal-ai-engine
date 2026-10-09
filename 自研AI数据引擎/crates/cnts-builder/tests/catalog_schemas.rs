use std::{fs::File, path::PathBuf};

use cnts_format::DatasetSchema;

#[test]
fn production_fact_schemas_are_valid_cnts_schemas() {
    let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .unwrap()
        .to_path_buf();
    let schemas = [
        "non-oil-sales.json",
        "oil-sales-plan.json",
        "oil-sales-daily.json",
        "oil-monthly-sales.json",
    ];

    for name in schemas {
        let path = workspace.join("schemas").join(name);
        let schema: DatasetSchema = serde_json::from_reader(File::open(&path).unwrap()).unwrap();
        schema
            .validate()
            .unwrap_or_else(|error| panic!("{} is invalid: {error}", path.display()));
    }
}
