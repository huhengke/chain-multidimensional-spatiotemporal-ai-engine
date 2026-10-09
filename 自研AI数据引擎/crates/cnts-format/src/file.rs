use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
};

use memmap2::Mmap;
use serde::{de::DeserializeOwned, Serialize};

use crate::{CntsError, FileHeader, FileType, Result, HEADER_SIZE};

pub fn write_cnts_file(
    path: &Path,
    file_type: FileType,
    generation_id: [u8; 16],
    schema_id: [u8; 16],
    block_count: u64,
    body: &[u8],
) -> Result<FileHeader> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let header = FileHeader::new(
        file_type,
        generation_id,
        schema_id,
        body.len() as u64,
        block_count,
        crc32c::crc32c(body),
    );
    let temp_path = path.with_extension("cnts.tmp");
    let mut file = File::create(&temp_path)?;
    file.write_all(&header.encode())?;
    file.write_all(body)?;
    file.sync_all()?;
    drop(file);
    if path.exists() {
        fs::remove_file(path)?;
    }
    fs::rename(temp_path, path)?;
    Ok(header)
}

pub fn write_json_file<T: Serialize>(
    path: &Path,
    file_type: FileType,
    generation_id: [u8; 16],
    schema_id: [u8; 16],
    value: &T,
) -> Result<FileHeader> {
    let body = serde_json::to_vec(value)?;
    write_cnts_file(path, file_type, generation_id, schema_id, 1, &body)
}

pub fn read_json_file<T: DeserializeOwned>(path: &Path, file_type: FileType) -> Result<T> {
    let mapped = MappedCntsFile::open(path, Some(file_type))?;
    Ok(serde_json::from_slice(mapped.body())?)
}

pub fn hash_file(path: &Path) -> Result<String> {
    let bytes = fs::read(path)?;
    Ok(blake3::hash(&bytes).to_hex().to_string())
}

pub struct MappedCntsFile {
    path: PathBuf,
    mmap: Mmap,
    header: FileHeader,
}

impl MappedCntsFile {
    pub fn open(path: &Path, expected_type: Option<FileType>) -> Result<Self> {
        let file = File::open(path)?;
        let mmap = unsafe { Mmap::map(&file)? };
        if mmap.len() < HEADER_SIZE {
            return Err(CntsError::InvalidHeader(format!(
                "{} is shorter than the CNTS header",
                path.display()
            )));
        }
        let header = FileHeader::decode(&mmap[..HEADER_SIZE])?;
        if let Some(expected) = expected_type {
            if header.file_type != expected {
                return Err(CntsError::UnexpectedFileType {
                    expected,
                    actual: header.file_type,
                });
            }
        }
        let start = header.data_offset as usize;
        let end = start
            .checked_add(header.data_len as usize)
            .ok_or_else(|| CntsError::InvalidHeader("data range overflow".to_string()))?;
        if start != HEADER_SIZE || end != mmap.len() {
            return Err(CntsError::InvalidHeader(format!(
                "declared data range {start}..{end} does not match file length {}",
                mmap.len()
            )));
        }
        let actual = crc32c::crc32c(&mmap[start..end]);
        if actual != header.body_crc32c {
            return Err(CntsError::ChecksumMismatch {
                path: path.to_path_buf(),
                expected: format!("{:08x}", header.body_crc32c),
                actual: format!("{actual:08x}"),
            });
        }
        Ok(Self {
            path: path.to_path_buf(),
            mmap,
            header,
        })
    }

    pub fn header(&self) -> &FileHeader {
        &self.header
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn body(&self) -> &[u8] {
        let start = self.header.data_offset as usize;
        &self.mmap[start..]
    }
}
