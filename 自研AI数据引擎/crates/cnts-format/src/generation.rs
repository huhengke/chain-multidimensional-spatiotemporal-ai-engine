use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};

use uuid::Uuid;

use crate::{
    hash_file, read_json_file, schema_id, Checksums, CntsError, FileType, GenerationManifest,
    MappedCntsFile, Result, Superblock,
};

fn hex_id(id: &[u8; 16]) -> String {
    id.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub fn read_current_generation(root: &Path) -> Result<PathBuf> {
    let current_path = root.join("CURRENT");
    let generation = fs::read_to_string(&current_path)?.trim().to_string();
    if generation.is_empty() {
        return Err(CntsError::Generation(format!(
            "{} is empty",
            current_path.display()
        )));
    }
    Uuid::parse_str(&generation).map_err(|error| {
        CntsError::Generation(format!("CURRENT contains invalid generation id: {error}"))
    })?;
    let path = root.join("generations").join(&generation);
    if !path.is_dir() {
        return Err(CntsError::Generation(format!(
            "CURRENT points to missing generation {}",
            path.display()
        )));
    }
    Ok(path)
}

pub fn verify_generation(generation_dir: &Path) -> Result<GenerationManifest> {
    let manifest_path = generation_dir.join("manifest.cnts");
    let checksums_path = generation_dir.join("checksums.cnts");
    let manifest: GenerationManifest = read_json_file(&manifest_path, FileType::Manifest)?;
    let checksums: Checksums = read_json_file(&checksums_path, FileType::Checksums)?;
    if generation_dir.file_name().and_then(|name| name.to_str())
        != Some(manifest.generation_id.as_str())
    {
        return Err(CntsError::InvalidData(
            "generation directory name does not match manifest generation id".to_string(),
        ));
    }
    if manifest.dataset_id != manifest.schema.dataset_id {
        return Err(CntsError::InvalidData(
            "manifest dataset id does not match embedded schema".to_string(),
        ));
    }
    manifest.schema.validate()?;
    let expected_schema_id = schema_id(&manifest.schema)?;
    if manifest.schema_id != hex_id(&expected_schema_id) {
        return Err(CntsError::InvalidData(
            "manifest schema id does not match embedded schema".to_string(),
        ));
    }
    if checksums.algorithm != "blake3" {
        return Err(CntsError::InvalidData(format!(
            "unsupported checksum algorithm {}",
            checksums.algorithm
        )));
    }
    let checksum_names: BTreeSet<&str> = checksums.files.keys().map(String::as_str).collect();
    let expected_checksum_names: BTreeSet<&str> = [
        "superblock.cnts",
        "manifest.cnts",
        "dictionaries.cnts",
        "inner.cnts",
        "store.cnts",
        "outer.cnts",
        "details-00000.cnts",
    ]
    .into_iter()
    .collect();
    if checksum_names != expected_checksum_names {
        return Err(CntsError::InvalidData(
            "checksums.cnts does not cover the exact required generation files".to_string(),
        ));
    }
    for (name, expected) in &checksums.files {
        let path = generation_dir.join(name);
        let actual = hash_file(&path)?;
        if &actual != expected {
            return Err(CntsError::ChecksumMismatch {
                path,
                expected: expected.clone(),
                actual,
            });
        }
    }
    let required = [
        ("superblock.cnts", FileType::Superblock),
        ("manifest.cnts", FileType::Manifest),
        ("dictionaries.cnts", FileType::Dictionaries),
        ("inner.cnts", FileType::Inner),
        ("store.cnts", FileType::Store),
        ("outer.cnts", FileType::Outer),
        ("details-00000.cnts", FileType::Details),
        ("checksums.cnts", FileType::Checksums),
    ];
    let expected_generation = Uuid::parse_str(&manifest.generation_id).map_err(|error| {
        CntsError::InvalidData(format!("invalid manifest generation id: {error}"))
    })?;
    let superblock: Superblock = read_json_file(
        &generation_dir.join("superblock.cnts"),
        FileType::Superblock,
    )?;
    if superblock.format != "CNTS"
        || superblock.generation_id != manifest.generation_id
        || superblock.schema_id != manifest.schema_id
        || superblock.dataset_id != manifest.dataset_id
    {
        return Err(CntsError::InvalidData(
            "superblock identity does not match manifest".to_string(),
        ));
    }
    for (name, file_type) in required {
        let mapped = MappedCntsFile::open(&generation_dir.join(name), Some(file_type))?;
        if mapped.header().generation_id != *expected_generation.as_bytes() {
            return Err(CntsError::InvalidData(format!(
                "{name} belongs to a different generation"
            )));
        }
        if mapped.header().schema_id != expected_schema_id {
            return Err(CntsError::InvalidData(format!(
                "{name} belongs to a different schema"
            )));
        }
    }
    for entry in &manifest.files {
        let path = generation_dir.join(&entry.name);
        let actual = fs::metadata(&path)?.len();
        if actual != entry.bytes {
            return Err(CntsError::InvalidData(format!(
                "manifest size for {} is {}, actual size is {actual}",
                entry.name, entry.bytes
            )));
        }
    }
    Ok(manifest)
}
