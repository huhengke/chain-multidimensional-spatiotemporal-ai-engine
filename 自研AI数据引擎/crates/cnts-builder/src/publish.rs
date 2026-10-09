use std::{
    collections::BTreeMap,
    ffi::OsStr,
    fs,
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};

use chrono::{DateTime, Utc};
use cnts_format::{
    hash_file, read_json_file, verify_generation, write_cnts_file, write_json_file, Checksums,
    CntsError, DatasetSchema, FileEntry, FileType, GenerationManifest, Result, Superblock,
    FORMAT_MAJOR, FORMAT_MINOR,
};
use uuid::Uuid;

use crate::tree::BuildArtifacts;

#[derive(Debug, Clone, serde::Serialize)]
pub struct BuildReport {
    pub generation_id: String,
    pub dataset_id: String,
    pub row_count: u64,
    pub node_count: u64,
    pub generation_dir: PathBuf,
}

struct CandidateGuard {
    path: PathBuf,
}

impl Drop for CandidateGuard {
    fn drop(&mut self) {
        if self.path.is_dir() {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

fn schema_hex(schema_id: &[u8; 16]) -> String {
    schema_id.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn new_generation_uuid(schema_id: &[u8; 16]) -> Uuid {
    static NONCE: AtomicU64 = AtomicU64::new(0);

    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let mut hasher = blake3::Hasher::new();
    hasher.update(schema_id);
    hasher.update(&timestamp.to_le_bytes());
    hasher.update(&std::process::id().to_le_bytes());
    hasher.update(&NONCE.fetch_add(1, Ordering::Relaxed).to_le_bytes());
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&hasher.finalize().as_bytes()[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Uuid::from_bytes(bytes)
}

fn file_entry(path: &Path, file_type: FileType, blocks: u64) -> Result<FileEntry> {
    Ok(FileEntry {
        name: path
            .file_name()
            .and_then(OsStr::to_str)
            .ok_or_else(|| CntsError::InvalidData("invalid file name".to_string()))?
            .to_string(),
        file_type: format!("{file_type:?}").to_lowercase(),
        bytes: fs::metadata(path)?.len(),
        blocks,
    })
}

pub fn write_and_publish(
    root: &Path,
    schema: &DatasetSchema,
    artifacts: BuildArtifacts,
) -> Result<BuildReport> {
    let schema_id = cnts_format::schema_id(schema)?;
    let generation = new_generation_uuid(&schema_id);
    let generation_id = generation.to_string();
    let temp_root = root.join("tmp");
    let candidate = temp_root.join(&generation_id);
    let generations_root = root.join("generations");
    let final_dir = generations_root.join(&generation_id);
    fs::create_dir_all(&temp_root)?;
    fs::create_dir_all(&generations_root)?;
    if candidate.exists() {
        fs::remove_dir_all(&candidate)?;
    }
    fs::create_dir_all(&candidate)?;
    let _candidate_guard = CandidateGuard {
        path: candidate.clone(),
    };

    let superblock = Superblock {
        format: "CNTS".to_string(),
        format_major: FORMAT_MAJOR,
        format_minor: FORMAT_MINOR,
        generation_id: generation_id.clone(),
        schema_id: schema_hex(&schema_id),
        dataset_id: schema.dataset_id.clone(),
    };
    let mut entries = Vec::new();
    let superblock_path = candidate.join("superblock.cnts");
    write_json_file(
        &superblock_path,
        FileType::Superblock,
        *generation.as_bytes(),
        schema_id,
        &superblock,
    )?;
    entries.push(file_entry(&superblock_path, FileType::Superblock, 1)?);

    let dictionary_path = candidate.join("dictionaries.cnts");
    write_cnts_file(
        &dictionary_path,
        FileType::Dictionaries,
        *generation.as_bytes(),
        schema_id,
        artifacts.dictionaries.fields.len() as u64,
        &artifacts.dictionaries.encode()?,
    )?;
    entries.push(file_entry(
        &dictionary_path,
        FileType::Dictionaries,
        artifacts.dictionaries.fields.len() as u64,
    )?);

    let inner_path = candidate.join("inner.cnts");
    write_cnts_file(
        &inner_path,
        FileType::Inner,
        *generation.as_bytes(),
        schema_id,
        artifacts.inner.node_count() as u64,
        &artifacts.inner.encode()?,
    )?;
    entries.push(file_entry(
        &inner_path,
        FileType::Inner,
        artifacts.inner.node_count() as u64,
    )?);

    let store_path = candidate.join("store.cnts");
    write_cnts_file(
        &store_path,
        FileType::Store,
        *generation.as_bytes(),
        schema_id,
        artifacts.store.metrics.len() as u64,
        &artifacts.store.encode()?,
    )?;
    entries.push(file_entry(
        &store_path,
        FileType::Store,
        artifacts.store.metrics.len() as u64,
    )?);

    let outer_path = candidate.join("outer.cnts");
    write_cnts_file(
        &outer_path,
        FileType::Outer,
        *generation.as_bytes(),
        schema_id,
        artifacts.outer.fields.len() as u64,
        &artifacts.outer.encode()?,
    )?;
    entries.push(file_entry(
        &outer_path,
        FileType::Outer,
        artifacts.outer.fields.len() as u64,
    )?);

    let detail_path = candidate.join("details-00000.cnts");
    write_cnts_file(
        &detail_path,
        FileType::Details,
        *generation.as_bytes(),
        schema_id,
        artifacts.details.block_count(),
        &artifacts.details.encode()?,
    )?;
    entries.push(file_entry(
        &detail_path,
        FileType::Details,
        artifacts.details.block_count(),
    )?);

    let manifest = GenerationManifest {
        generation_id: generation_id.clone(),
        schema_id: schema_hex(&schema_id),
        dataset_id: schema.dataset_id.clone(),
        created_at: DateTime::<Utc>::from(SystemTime::now()).to_rfc3339(),
        row_count: artifacts.row_count,
        node_count: artifacts.inner.node_count() as u64,
        schema: schema.clone(),
        files: entries,
    };
    let manifest_path = candidate.join("manifest.cnts");
    write_json_file(
        &manifest_path,
        FileType::Manifest,
        *generation.as_bytes(),
        schema_id,
        &manifest,
    )?;

    let checksum_names = [
        "superblock.cnts",
        "manifest.cnts",
        "dictionaries.cnts",
        "inner.cnts",
        "store.cnts",
        "outer.cnts",
        "details-00000.cnts",
    ];
    let mut checksum_files = BTreeMap::new();
    for name in checksum_names {
        checksum_files.insert(name.to_string(), hash_file(&candidate.join(name))?);
    }
    let checksums = Checksums {
        algorithm: "blake3".to_string(),
        files: checksum_files,
    };
    write_json_file(
        &candidate.join("checksums.cnts"),
        FileType::Checksums,
        *generation.as_bytes(),
        schema_id,
        &checksums,
    )?;

    verify_generation(&candidate)?;
    fs::rename(&candidate, &final_dir)?;
    write_current_atomic(root, &generation_id)?;
    Ok(BuildReport {
        generation_id,
        dataset_id: schema.dataset_id.clone(),
        row_count: artifacts.row_count,
        node_count: artifacts.inner.node_count() as u64,
        generation_dir: final_dir,
    })
}

pub fn rollback(root: &Path) -> Result<String> {
    let current = fs::read_to_string(root.join("CURRENT"))?.trim().to_string();
    let mut generations = Vec::new();
    for entry in fs::read_dir(root.join("generations"))? {
        let entry = entry?;
        if !entry.path().is_dir() {
            continue;
        }
        let manifest: GenerationManifest =
            match read_json_file(&entry.path().join("manifest.cnts"), FileType::Manifest) {
                Ok(manifest) => manifest,
                Err(_) => continue,
            };
        if manifest.generation_id != current {
            generations.push((manifest.created_at, manifest.generation_id));
        }
    }
    generations.sort();
    let (_, target) = generations
        .pop()
        .ok_or_else(|| CntsError::Generation("no previous generation is available".to_string()))?;
    verify_generation(&root.join("generations").join(&target))?;
    write_current_atomic(root, &target)?;
    Ok(target)
}

fn write_current_atomic(root: &Path, generation_id: &str) -> Result<()> {
    fs::create_dir_all(root)?;
    let target = root.join("CURRENT");
    let candidate = root.join("CURRENT.next");
    fs::write(&candidate, format!("{generation_id}\n"))?;
    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&candidate)?;
    file.sync_all()?;
    drop(file);
    atomic_replace(&candidate, &target)
}

#[cfg(windows)]
fn atomic_replace(source: &Path, target: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source_wide: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let target_wide: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    let result = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            target_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(())
}

#[cfg(not(windows))]
fn atomic_replace(source: &Path, target: &Path) -> Result<()> {
    fs::rename(source, target)?;
    Ok(())
}
