mod executor;
mod loader;
mod planner;

use std::path::Path;

use cnts_format::{QueryPlan, QueryResult, Result};

pub use executor::{execute, format_scaled};
pub use loader::LoadedGeneration;

pub fn query(root: &Path, plan: &QueryPlan) -> Result<QueryResult> {
    let generation = LoadedGeneration::open(root)?;
    execute(&generation, plan)
}

pub fn query_generation(root: &Path, generation_id: &str, plan: &QueryPlan) -> Result<QueryResult> {
    let generation = LoadedGeneration::open_generation(root, generation_id)?;
    execute(&generation, plan)
}
