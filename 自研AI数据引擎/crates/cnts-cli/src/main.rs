use std::{fs::File, path::PathBuf};

use anyhow::Context;
use clap::{Parser, Subcommand};
use cnts_format::{read_current_generation, verify_generation, QueryPlan};
use cnts_query::LoadedGeneration;
use serde::Serialize;

#[derive(Debug, Parser)]
#[command(name = "cnts", version, about = "CNTS native wisdom-tree engine")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Build {
        #[arg(long)]
        schema: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        root: PathBuf,
    },
    Inspect {
        #[arg(long)]
        root: PathBuf,
    },
    Verify {
        #[arg(long)]
        root: PathBuf,
    },
    Query {
        #[arg(long)]
        root: PathBuf,
        #[arg(long)]
        plan: PathBuf,
    },
    BatchQuery {
        #[arg(long)]
        root: PathBuf,
        #[arg(long)]
        plans: PathBuf,
    },
    Rollback {
        #[arg(long)]
        root: PathBuf,
    },
}

#[derive(Debug, Serialize)]
struct InspectReport {
    generation_id: String,
    dataset_id: String,
    row_count: u64,
    node_count: u64,
    time_nodes: u64,
    branch_nodes: u64,
    overcapacity_nodes: u64,
    dictionary_fields: usize,
    outer_fields: usize,
    metric_columns: usize,
    detail_columns: usize,
}

fn print_json<T: Serialize>(value: &T) -> anyhow::Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Build {
            schema,
            input,
            root,
        } => {
            let report = cnts_builder::build_generation(&schema, &input, &root)
                .with_context(|| format!("failed to build generation from {}", input.display()))?;
            print_json(&report)?;
        }
        Command::Inspect { root } => {
            let generation = LoadedGeneration::open(&root)?;
            let flags = &generation.inner.flags;
            print_json(&InspectReport {
                generation_id: generation.manifest.generation_id.clone(),
                dataset_id: generation.manifest.dataset_id.clone(),
                row_count: generation.manifest.row_count,
                node_count: generation.manifest.node_count,
                time_nodes: flags.iter().filter(|flag| **flag & 2 != 0).count() as u64,
                branch_nodes: flags.iter().filter(|flag| **flag & 4 != 0).count() as u64,
                overcapacity_nodes: flags.iter().filter(|flag| **flag & 8 != 0).count() as u64,
                dictionary_fields: generation.dictionaries.fields.len(),
                outer_fields: generation.outer.fields.len(),
                metric_columns: generation.store.metrics.len(),
                detail_columns: generation.details.columns.len(),
            })?;
        }
        Command::Verify { root } => {
            let path = read_current_generation(&root)?;
            let manifest = verify_generation(&path)?;
            print_json(&manifest)?;
        }
        Command::Query { root, plan } => {
            let plan: QueryPlan = serde_json::from_reader(
                File::open(&plan)
                    .with_context(|| format!("failed to open query plan {}", plan.display()))?,
            )?;
            let result = cnts_query::query(&root, &plan)?;
            print_json(&result)?;
        }
        Command::BatchQuery { root, plans } => {
            let plans: Vec<QueryPlan> = serde_json::from_reader(
                File::open(&plans)
                    .with_context(|| format!("failed to open query plans {}", plans.display()))?,
            )?;
            let generation = LoadedGeneration::open(&root)?;
            let results = plans
                .iter()
                .map(|plan| cnts_query::execute(&generation, plan))
                .collect::<cnts_format::Result<Vec<_>>>()?;
            print_json(&results)?;
        }
        Command::Rollback { root } => {
            let generation_id = cnts_builder::rollback(&root)?;
            print_json(&serde_json::json!({ "generation_id": generation_id }))?;
        }
    }
    Ok(())
}
