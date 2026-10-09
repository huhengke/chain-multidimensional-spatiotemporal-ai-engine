use crate::{CntsError, Result};

pub const HEADER_SIZE: usize = 256;
pub const FORMAT_MAJOR: u16 = 1;
pub const FORMAT_MINOR: u16 = 0;
const MAGIC: &[u8; 4] = b"CNTS";
const HEADER_CRC_OFFSET: usize = HEADER_SIZE - 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum FileType {
    Superblock = 1,
    Manifest = 2,
    Dictionaries = 3,
    Inner = 4,
    Store = 5,
    Outer = 6,
    Details = 7,
    Checksums = 8,
}

impl TryFrom<u8> for FileType {
    type Error = CntsError;

    fn try_from(value: u8) -> Result<Self> {
        match value {
            1 => Ok(Self::Superblock),
            2 => Ok(Self::Manifest),
            3 => Ok(Self::Dictionaries),
            4 => Ok(Self::Inner),
            5 => Ok(Self::Store),
            6 => Ok(Self::Outer),
            7 => Ok(Self::Details),
            8 => Ok(Self::Checksums),
            _ => Err(CntsError::InvalidHeader(format!(
                "unknown file type {value}"
            ))),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileHeader {
    pub file_type: FileType,
    pub generation_id: [u8; 16],
    pub schema_id: [u8; 16],
    pub data_offset: u64,
    pub data_len: u64,
    pub block_count: u64,
    pub body_crc32c: u32,
    pub flags: u32,
}

impl FileHeader {
    pub fn new(
        file_type: FileType,
        generation_id: [u8; 16],
        schema_id: [u8; 16],
        data_len: u64,
        block_count: u64,
        body_crc32c: u32,
    ) -> Self {
        Self {
            file_type,
            generation_id,
            schema_id,
            data_offset: HEADER_SIZE as u64,
            data_len,
            block_count,
            body_crc32c,
            flags: 0,
        }
    }

    pub fn encode(&self) -> [u8; HEADER_SIZE] {
        let mut bytes = [0_u8; HEADER_SIZE];
        bytes[0..4].copy_from_slice(MAGIC);
        bytes[4..6].copy_from_slice(&FORMAT_MAJOR.to_le_bytes());
        bytes[6..8].copy_from_slice(&FORMAT_MINOR.to_le_bytes());
        bytes[8] = 1;
        bytes[9] = self.file_type as u8;
        bytes[10..12].copy_from_slice(&(HEADER_SIZE as u16).to_le_bytes());
        bytes[12..16].copy_from_slice(&self.flags.to_le_bytes());
        bytes[16..32].copy_from_slice(&self.generation_id);
        bytes[32..48].copy_from_slice(&self.schema_id);
        bytes[48..56].copy_from_slice(&self.data_offset.to_le_bytes());
        bytes[56..64].copy_from_slice(&self.data_len.to_le_bytes());
        bytes[64..72].copy_from_slice(&self.block_count.to_le_bytes());
        bytes[72..76].copy_from_slice(&self.body_crc32c.to_le_bytes());
        let header_crc = crc32c::crc32c(&bytes[..HEADER_CRC_OFFSET]);
        bytes[HEADER_CRC_OFFSET..].copy_from_slice(&header_crc.to_le_bytes());
        bytes
    }

    pub fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.len() < HEADER_SIZE {
            return Err(CntsError::InvalidHeader(format!(
                "header is {} bytes, expected {HEADER_SIZE}",
                bytes.len()
            )));
        }
        if &bytes[0..4] != MAGIC {
            return Err(CntsError::InvalidHeader("magic is not CNTS".to_string()));
        }
        let major = u16::from_le_bytes(bytes[4..6].try_into().unwrap());
        let minor = u16::from_le_bytes(bytes[6..8].try_into().unwrap());
        if major != FORMAT_MAJOR || minor > FORMAT_MINOR {
            return Err(CntsError::UnsupportedVersion { major, minor });
        }
        if bytes[8] != 1 {
            return Err(CntsError::InvalidHeader(
                "only little-endian files are supported".to_string(),
            ));
        }
        let header_size = u16::from_le_bytes(bytes[10..12].try_into().unwrap()) as usize;
        if header_size != HEADER_SIZE {
            return Err(CntsError::InvalidHeader(format!(
                "header size is {header_size}, expected {HEADER_SIZE}"
            )));
        }
        let expected_crc =
            u32::from_le_bytes(bytes[HEADER_CRC_OFFSET..HEADER_SIZE].try_into().unwrap());
        let actual_crc = crc32c::crc32c(&bytes[..HEADER_CRC_OFFSET]);
        if expected_crc != actual_crc {
            return Err(CntsError::InvalidHeader(format!(
                "header CRC32C mismatch: expected {expected_crc:08x}, actual {actual_crc:08x}"
            )));
        }
        let mut generation_id = [0_u8; 16];
        generation_id.copy_from_slice(&bytes[16..32]);
        let mut schema_id = [0_u8; 16];
        schema_id.copy_from_slice(&bytes[32..48]);
        Ok(Self {
            file_type: FileType::try_from(bytes[9])?,
            generation_id,
            schema_id,
            data_offset: u64::from_le_bytes(bytes[48..56].try_into().unwrap()),
            data_len: u64::from_le_bytes(bytes[56..64].try_into().unwrap()),
            block_count: u64::from_le_bytes(bytes[64..72].try_into().unwrap()),
            body_crc32c: u32::from_le_bytes(bytes[72..76].try_into().unwrap()),
            flags: u32::from_le_bytes(bytes[12..16].try_into().unwrap()),
        })
    }
}
