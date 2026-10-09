use std::path::PathBuf;

#[derive(Debug, thiserror::Error)]
pub enum CntsError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("invalid CNTS header: {0}")]
    InvalidHeader(String),
    #[error("unsupported CNTS format version {major}.{minor}")]
    UnsupportedVersion { major: u16, minor: u16 },
    #[error("unexpected file type: expected {expected:?}, found {actual:?}")]
    UnexpectedFileType {
        expected: crate::FileType,
        actual: crate::FileType,
    },
    #[error("checksum mismatch for {path}: expected {expected}, actual {actual}")]
    ChecksumMismatch {
        path: PathBuf,
        expected: String,
        actual: String,
    },
    #[error("invalid data: {0}")]
    InvalidData(String),
    #[error("query is not supported: {0}")]
    UnsupportedQuery(String),
    #[error("generation error: {0}")]
    Generation(String),
    #[error("numeric overflow while processing {0}")]
    NumericOverflow(String),
}

pub type Result<T> = std::result::Result<T, CntsError>;
