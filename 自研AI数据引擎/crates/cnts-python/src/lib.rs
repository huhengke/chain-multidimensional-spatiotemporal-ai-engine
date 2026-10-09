use std::{
    collections::{BTreeSet, HashMap, VecDeque},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
};

use chrono::{Duration, NaiveDate};
use cnts_format::{
    read_current_generation, verify_generation as verify_generation_files, QueryPlan, NONE_ORDINAL,
};
use cnts_query::{format_scaled, LoadedGeneration};
use pyo3::{exceptions::PyRuntimeError, prelude::*};
use uuid::Uuid;

const DEFAULT_CACHE_CAPACITY: usize = 8;

struct GenerationCache {
    capacity: usize,
    entries: HashMap<String, Arc<LoadedGeneration>>,
    order: VecDeque<String>,
    hits: u64,
    misses: u64,
}

impl GenerationCache {
    fn new() -> Self {
        let capacity = std::env::var("CNTS_GENERATION_CACHE_SIZE")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_CACHE_CAPACITY);
        Self {
            capacity,
            entries: HashMap::new(),
            order: VecDeque::new(),
            hits: 0,
            misses: 0,
        }
    }

    fn touch(&mut self, key: &str) {
        self.order.retain(|candidate| candidate != key);
        self.order.push_back(key.to_string());
    }

    fn get(&mut self, key: &str) -> Option<Arc<LoadedGeneration>> {
        let generation = self.entries.get(key).cloned();
        if generation.is_some() {
            self.hits += 1;
            self.touch(key);
        }
        generation
    }

    fn insert(&mut self, key: String, generation: Arc<LoadedGeneration>) {
        self.misses += 1;
        self.entries.insert(key.clone(), generation);
        self.touch(&key);
        while self.entries.len() > self.capacity {
            if let Some(oldest) = self.order.pop_front() {
                self.entries.remove(&oldest);
            }
        }
    }
}

fn generation_cache() -> &'static Mutex<GenerationCache> {
    static CACHE: OnceLock<Mutex<GenerationCache>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(GenerationCache::new()))
}

fn canonical_root(root: &str) -> Result<PathBuf, std::io::Error> {
    Path::new(root).canonicalize()
}

fn cached_generation(root: &str, generation_id: &str) -> PyResult<Arc<LoadedGeneration>> {
    Uuid::parse_str(generation_id).map_err(runtime_error)?;
    let root = canonical_root(root).map_err(runtime_error)?;
    let key = format!("{}\u{1f}{generation_id}", root.display());
    let mut cache = generation_cache()
        .lock()
        .map_err(|_| runtime_error("generation cache lock is poisoned"))?;
    if let Some(generation) = cache.get(&key) {
        return Ok(generation);
    }
    let generation =
        Arc::new(LoadedGeneration::open_generation(&root, generation_id).map_err(runtime_error)?);
    cache.insert(key, Arc::clone(&generation));
    Ok(generation)
}

fn runtime_error(error: impl std::fmt::Display) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

#[pyfunction]
fn verify(root: &str) -> PyResult<String> {
    let generation = read_current_generation(Path::new(root)).map_err(runtime_error)?;
    let manifest = verify_generation_files(&generation).map_err(runtime_error)?;
    serde_json::to_string(&manifest).map_err(runtime_error)
}

#[pyfunction]
fn verify_generation(root: &str, generation_id: &str) -> PyResult<String> {
    Uuid::parse_str(generation_id).map_err(runtime_error)?;
    let generation = Path::new(root).join("generations").join(generation_id);
    let manifest = verify_generation_files(&generation).map_err(runtime_error)?;
    serde_json::to_string(&manifest).map_err(runtime_error)
}

#[pyfunction]
fn query(root: &str, plan_json: &str) -> PyResult<String> {
    let plan: QueryPlan = serde_json::from_str(plan_json).map_err(runtime_error)?;
    let result = cnts_query::query(Path::new(root), &plan).map_err(runtime_error)?;
    serde_json::to_string(&result).map_err(runtime_error)
}

#[pyfunction]
fn query_generation(root: &str, generation_id: &str, plan_json: &str) -> PyResult<String> {
    let plan: QueryPlan = serde_json::from_str(plan_json).map_err(runtime_error)?;
    let generation = cached_generation(root, generation_id)?;
    let result = cnts_query::execute(&generation, &plan).map_err(runtime_error)?;
    serde_json::to_string(&result).map_err(runtime_error)
}

fn dimension_names(generation: &LoadedGeneration) -> Vec<String> {
    let schema = &generation.manifest.schema;
    let mut names = Vec::new();
    for name in schema
        .common_dimensions
        .iter()
        .chain(schema.outer_dimensions.iter())
        .chain(schema.growth_dimensions.iter())
    {
        if !names.contains(name) {
            names.push(name.clone());
        }
    }
    names
}

fn evenly_spaced(values: &[usize], count: usize) -> Vec<usize> {
    if count == 0 || values.is_empty() {
        return Vec::new();
    }
    if values.len() <= count {
        return values.to_vec();
    }
    if count == 1 {
        return vec![values[values.len() / 2]];
    }
    (0..count)
        .map(|index| {
            let source = index * (values.len() - 1) / (count - 1);
            values[source]
        })
        .collect()
}

fn sampled_ordinals(generation: &LoadedGeneration, limit: usize) -> Vec<usize> {
    let limit = limit.clamp(6, 1_000);
    let inner = &generation.inner;
    let mut selected = BTreeSet::from([0_usize]);
    let trunks = inner
        .flags
        .iter()
        .enumerate()
        .filter_map(|(ordinal, flag)| (*flag & 2 != 0).then_some(ordinal))
        .collect::<Vec<_>>();
    let branches = inner
        .flags
        .iter()
        .enumerate()
        .filter_map(|(ordinal, flag)| (*flag & 4 != 0).then_some(ordinal))
        .collect::<Vec<_>>();

    let trunk_target = trunks.len().min((limit / 6).clamp(3, 12));
    selected.extend(evenly_spaced(&trunks, trunk_target));

    let branch_target = limit.saturating_sub(selected.len());
    let mut candidates = evenly_spaced(&branches, branch_target);
    candidates.extend(branches.iter().copied());
    for candidate in candidates {
        if selected.len() >= limit || selected.contains(&candidate) {
            continue;
        }
        let mut chain = Vec::new();
        let mut current = candidate as u64;
        while current != NONE_ORDINAL && !selected.contains(&(current as usize)) {
            chain.push(current as usize);
            current = inner.parent_ordinal[current as usize];
        }
        chain.reverse();
        if selected.len() + chain.len() <= limit {
            selected.extend(chain);
        }
    }
    selected.into_iter().collect()
}

fn topology_payload(generation: &LoadedGeneration, limit: usize) -> serde_json::Value {
    let inner = &generation.inner;
    let schema = &generation.manifest.schema;
    let dimensions = dimension_names(generation);
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).expect("valid epoch");
    let node_count = inner.node_count();
    let mut axis_names = Vec::with_capacity(node_count);
    let mut values = Vec::with_capacity(node_count);
    let mut paths: Vec<String> = Vec::with_capacity(node_count);
    let mut time_buckets: Vec<Option<String>> = Vec::with_capacity(node_count);

    for ordinal in 0..node_count {
        let flag = inner.flags[ordinal];
        let (axis, value) = if flag & 1 != 0 {
            ("base".to_string(), "*".to_string())
        } else if flag & 2 != 0 {
            let day = inner.value_id[ordinal] as i32;
            let value = epoch
                .checked_add_signed(Duration::days(i64::from(day)))
                .map(|item| item.format("%Y-%m-%d").to_string())
                .unwrap_or_else(|| day.to_string());
            (schema.time_field.clone(), value)
        } else {
            let axis = inner.axis_id[ordinal]
                .checked_sub(2)
                .and_then(|index| dimensions.get(index as usize))
                .cloned()
                .unwrap_or_else(|| format!("axis_{}", inner.axis_id[ordinal]));
            let value = generation
                .dictionaries
                .field(&axis)
                .and_then(|dictionary| dictionary.values.get(inner.value_id[ordinal] as usize))
                .cloned()
                .unwrap_or_else(|| inner.value_id[ordinal].to_string());
            (axis, value)
        };
        let parent = inner.parent_ordinal[ordinal];
        let path = if parent == NONE_ORDINAL {
            "/".to_string()
        } else {
            format!(
                "{}/{}={}",
                paths[parent as usize].trim_end_matches('/'),
                axis,
                value
            )
        };
        let time_bucket = if flag & 2 != 0 {
            Some(value.clone())
        } else if parent != NONE_ORDINAL {
            time_buckets[parent as usize].clone()
        } else {
            None
        };
        axis_names.push(axis);
        values.push(value);
        paths.push(path);
        time_buckets.push(time_bucket);
    }

    let selected = sampled_ordinals(generation, limit);
    let selected_ids = selected.iter().copied().collect::<BTreeSet<_>>();
    let nodes = selected
        .into_iter()
        .map(|ordinal| {
            let flag = inner.flags[ordinal];
            let role = if flag & 1 != 0 {
                "base"
            } else if flag & 2 != 0 {
                "trunk"
            } else {
                "branch"
            };
            let node_id = Uuid::from_bytes(inner.logical_node_id[ordinal]).to_string();
            let parent_ordinal = inner.parent_ordinal[ordinal];
            let parent_id = (parent_ordinal != NONE_ORDINAL).then(|| {
                Uuid::from_bytes(inner.logical_node_id[parent_ordinal as usize]).to_string()
            });
            let aggregations = generation
                .store
                .metrics
                .iter()
                .filter(|metric| metric.name != "__record_count")
                .map(|metric| {
                    (
                        metric.name.clone(),
                        serde_json::Value::String(format_scaled(
                            metric.values[ordinal],
                            metric.scale,
                        )),
                    )
                })
                .collect::<serde_json::Map<_, _>>();
            serde_json::json!({
                "node_id": node_id,
                "dataset": generation.manifest.dataset_id,
                "level": inner.branch_depth[ordinal],
                "level_name": axis_names[ordinal],
                "value": values[ordinal],
                "parent_id": parent_id,
                "path": paths[ordinal],
                "record_count": inner.detail_count[ordinal],
                "aggregations": aggregations,
                "grown": inner.first_child_ordinal[ordinal] != NONE_ORDINAL,
                "topology": {
                    "version": 1,
                    "role": role,
                    "helix_layer": inner.temporal_layer[ordinal],
                    "temporal_layer": inner.temporal_layer[ordinal],
                    "time_bucket": time_buckets[ordinal],
                    "time_granularity": schema.time_format,
                    "branch_depth": inner.branch_depth[ordinal],
                    "growth_parent_id": if role == "branch" { parent_id.clone() } else { None },
                    "growth_dimension": if role == "branch" { Some(axis_names[ordinal].clone()) } else { None },
                    "capacity_snapshot": inner.detail_count[ordinal],
                    "growth_version": schema.schema_major,
                    "ring_index": ordinal,
                    "strand_a_prev": null,
                    "strand_a_next": null,
                    "strand_b_prev": null,
                    "strand_b_next": null,
                    "pair_node_id": null,
                    "feature_slots": {
                        "axis_id": inner.axis_id[ordinal],
                        "generation_id": generation.manifest.generation_id,
                        "parent_in_slice": parent_ordinal == NONE_ORDINAL || selected_ids.contains(&(parent_ordinal as usize)),
                    },
                },
            })
        })
        .collect::<Vec<_>>();

    let time_nodes = inner.flags.iter().filter(|flag| **flag & 2 != 0).count();
    let branch_nodes = inner.flags.iter().filter(|flag| **flag & 4 != 0).count();
    let grown_nodes = inner
        .first_child_ordinal
        .iter()
        .filter(|child| **child != NONE_ORDINAL)
        .count();
    let temporal_layers = inner.temporal_layer.iter().copied().max().unwrap_or(0);
    serde_json::json!({
        "summary": {
            "dataset": generation.manifest.dataset_id,
            "generation_id": generation.manifest.generation_id,
            "topology_version": 1,
            "node_count": node_count,
            "helix_layers": temporal_layers,
            "temporal_layers": temporal_layers,
            "paired_nodes": 0,
            "strand_a_edges": time_nodes.saturating_sub(1),
            "strand_b_edges": branch_nodes.saturating_sub(1),
            "trunk_nodes": time_nodes,
            "branch_nodes": branch_nodes,
            "grown_nodes": grown_nodes,
            "growth_threshold": schema.growth_threshold,
            "physical_levels": schema.growth_dimensions,
            "valid": true,
            "errors": [],
            "dna_is_topology_not_query_level": true,
            "query_path": ["foundation", "middle_inner", "middle_store", "outer_feature", "fact_result"],
        },
        "nodes": nodes,
    })
}

#[pyfunction]
fn topology_generation(root: &str, generation_id: &str, limit: usize) -> PyResult<String> {
    let generation = cached_generation(root, generation_id)?;
    serde_json::to_string(&topology_payload(&generation, limit)).map_err(runtime_error)
}

#[pyfunction]
fn generation_cache_info() -> PyResult<String> {
    let cache = generation_cache()
        .lock()
        .map_err(|_| runtime_error("generation cache lock is poisoned"))?;
    serde_json::to_string(&serde_json::json!({
        "capacity": cache.capacity,
        "entries": cache.entries.len(),
        "hits": cache.hits,
        "misses": cache.misses,
    }))
    .map_err(runtime_error)
}

#[pyfunction]
fn clear_generation_cache() -> PyResult<()> {
    let mut cache = generation_cache()
        .lock()
        .map_err(|_| runtime_error("generation cache lock is poisoned"))?;
    cache.entries.clear();
    cache.order.clear();
    cache.hits = 0;
    cache.misses = 0;
    Ok(())
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(verify, module)?)?;
    module.add_function(wrap_pyfunction!(verify_generation, module)?)?;
    module.add_function(wrap_pyfunction!(query, module)?)?;
    module.add_function(wrap_pyfunction!(query_generation, module)?)?;
    module.add_function(wrap_pyfunction!(topology_generation, module)?)?;
    module.add_function(wrap_pyfunction!(generation_cache_info, module)?)?;
    module.add_function(wrap_pyfunction!(clear_generation_cache, module)?)?;
    Ok(())
}
