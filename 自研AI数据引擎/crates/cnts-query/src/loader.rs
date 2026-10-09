use std::path::{Path, PathBuf};

use cnts_format::{
    read_current_generation, verify_generation, CntsError, ColumnKind, ColumnValues, DetailData,
    DictionarySet, FileType, GenerationManifest, InnerColumns, MappedCntsFile, OuterIndex, Result,
    StoreData, NONE_ORDINAL,
};
use uuid::Uuid;

pub struct LoadedGeneration {
    pub path: PathBuf,
    pub manifest: GenerationManifest,
    pub dictionaries: DictionarySet,
    pub inner: InnerColumns,
    pub store: StoreData,
    pub outer: OuterIndex,
    pub details: DetailData,
}

impl LoadedGeneration {
    pub fn open(root: &Path) -> Result<Self> {
        let path = read_current_generation(root)?;
        Self::open_path(path)
    }

    pub fn open_generation(root: &Path, generation_id: &str) -> Result<Self> {
        Uuid::parse_str(generation_id).map_err(|error| {
            CntsError::InvalidData(format!("invalid generation id {generation_id}: {error}"))
        })?;
        Self::open_path(root.join("generations").join(generation_id))
    }

    fn open_path(path: PathBuf) -> Result<Self> {
        let manifest = verify_generation(&path)?;
        let dictionaries_file = MappedCntsFile::open(
            &path.join("dictionaries.cnts"),
            Some(FileType::Dictionaries),
        )?;
        let dictionaries = DictionarySet::decode(dictionaries_file.body())?;
        let inner_file = MappedCntsFile::open(&path.join("inner.cnts"), Some(FileType::Inner))?;
        let inner = InnerColumns::decode(inner_file.body())?;
        let store_file = MappedCntsFile::open(&path.join("store.cnts"), Some(FileType::Store))?;
        let store = StoreData::decode(store_file.body())?;
        let outer_file = MappedCntsFile::open(&path.join("outer.cnts"), Some(FileType::Outer))?;
        let outer = OuterIndex::decode(outer_file.body())?;
        let details_file =
            MappedCntsFile::open(&path.join("details-00000.cnts"), Some(FileType::Details))?;
        let details = DetailData::decode(details_file.body())?;
        for (name, declared, actual) in [
            (
                "dictionaries.cnts",
                dictionaries_file.header().block_count,
                dictionaries.fields.len() as u64,
            ),
            (
                "inner.cnts",
                inner_file.header().block_count,
                inner.node_count() as u64,
            ),
            (
                "store.cnts",
                store_file.header().block_count,
                store.metrics.len() as u64,
            ),
            (
                "outer.cnts",
                outer_file.header().block_count,
                outer.fields.len() as u64,
            ),
            (
                "details-00000.cnts",
                details_file.header().block_count,
                details.block_count(),
            ),
        ] {
            if declared != actual {
                return Err(CntsError::InvalidData(format!(
                    "{name} header declares {declared} blocks, decoded {actual}"
                )));
            }
        }
        let loaded = Self {
            path,
            manifest,
            dictionaries,
            inner,
            store,
            outer,
            details,
        };
        loaded.validate_cross_file()?;
        Ok(loaded)
    }

    fn validate_cross_file(&self) -> Result<()> {
        self.manifest.schema.validate()?;
        let node_count = self.inner.node_count() as u64;
        if node_count != self.manifest.node_count || node_count != self.store.node_count {
            return Err(CntsError::InvalidData(format!(
                "node count mismatch: manifest={}, inner={node_count}, store={}",
                self.manifest.node_count, self.store.node_count
            )));
        }
        if self.details.row_count != self.manifest.row_count {
            return Err(CntsError::InvalidData(format!(
                "row count mismatch: manifest={}, details={}",
                self.manifest.row_count, self.details.row_count
            )));
        }
        if self.inner.node_count() == 0
            || self.inner.parent_ordinal[0] != NONE_ORDINAL
            || self.inner.axis_id[0] != 0
        {
            return Err(CntsError::InvalidData(
                "inner topology must start with one root node".to_string(),
            ));
        }
        for ordinal in 0..self.inner.node_count() {
            if self.inner.aggregate_offset[ordinal] >= self.store.node_count {
                return Err(CntsError::InvalidData(format!(
                    "node {ordinal} aggregate offset is out of bounds"
                )));
            }
            let detail_end = self.inner.detail_start[ordinal]
                .checked_add(self.inner.detail_count[ordinal])
                .ok_or_else(|| {
                    CntsError::InvalidData(format!("node {ordinal} detail range overflows"))
                })?;
            if detail_end > self.details.row_count {
                return Err(CntsError::InvalidData(format!(
                    "node {ordinal} detail range exceeds row count"
                )));
            }
        }

        let schema = &self.manifest.schema;
        let time = self.details.column(&schema.time_field).ok_or_else(|| {
            CntsError::InvalidData(format!(
                "time detail column {} is missing",
                schema.time_field
            ))
        })?;
        if time.kind != ColumnKind::Time {
            return Err(CntsError::InvalidData(format!(
                "time detail column {} has the wrong type",
                schema.time_field
            )));
        }

        for field in schema
            .common_dimensions
            .iter()
            .chain(schema.outer_dimensions.iter())
        {
            let dictionary = self
                .dictionaries
                .field(field)
                .ok_or_else(|| CntsError::InvalidData(format!("dictionary {field} is missing")))?;
            let column = self.details.column(field).ok_or_else(|| {
                CntsError::InvalidData(format!("dimension detail column {field} is missing"))
            })?;
            let values = match &column.values {
                ColumnValues::U32(values) if column.kind == ColumnKind::Dictionary => values,
                _ => {
                    return Err(CntsError::InvalidData(format!(
                        "dimension detail column {field} has the wrong type"
                    )))
                }
            };
            for (row, value_id) in values.iter().enumerate() {
                let is_null = column.null_bitmap[row / 8] & (1 << (row % 8)) != 0;
                if is_null != (*value_id == u32::MAX) {
                    return Err(CntsError::InvalidData(format!(
                        "dimension {field} row {row} has inconsistent null encoding"
                    )));
                }
                if !is_null && *value_id as usize >= dictionary.values.len() {
                    return Err(CntsError::InvalidData(format!(
                        "dimension {field} row {row} has invalid dictionary id {value_id}"
                    )));
                }
                if schema.common_dimensions.contains(field) && is_null {
                    return Err(CntsError::InvalidData(format!(
                        "common dimension {field} contains null at row {row}"
                    )));
                }
            }
        }

        let record_count = self.store.metric("__record_count").ok_or_else(|| {
            CntsError::InvalidData("store record count metric is missing".to_string())
        })?;
        if record_count.scale != 0 {
            return Err(CntsError::InvalidData(
                "store record count metric must have scale 0".to_string(),
            ));
        }
        for metric in &schema.metrics {
            let store_metric = self.store.metric(&metric.name).ok_or_else(|| {
                CntsError::InvalidData(format!("store metric {} is missing", metric.name))
            })?;
            let detail_metric = self.details.column(&metric.name).ok_or_else(|| {
                CntsError::InvalidData(format!("detail metric {} is missing", metric.name))
            })?;
            if store_metric.scale != metric.scale
                || detail_metric.scale != metric.scale
                || detail_metric.kind != ColumnKind::Metric
            {
                return Err(CntsError::InvalidData(format!(
                    "metric {} scale or type does not match schema",
                    metric.name
                )));
            }
        }

        for field in &schema.outer_dimensions {
            let dictionary = self.dictionaries.field(field).unwrap();
            let outer_field = self
                .outer
                .field(field)
                .ok_or_else(|| CntsError::InvalidData(format!("outer index {field} is missing")))?;
            for posting in &outer_field.postings {
                if posting.value_id as usize >= dictionary.values.len() {
                    return Err(CntsError::InvalidData(format!(
                        "outer index {field} has invalid dictionary id {}",
                        posting.value_id
                    )));
                }
                if posting
                    .rows
                    .iter()
                    .any(|row| *row as u64 >= self.details.row_count)
                {
                    return Err(CntsError::InvalidData(format!(
                        "outer index {field} contains an out-of-range row id"
                    )));
                }
            }
        }
        Ok(())
    }
}
