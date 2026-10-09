use std::io::{Cursor, Read};

use crate::{CntsError, Result};

pub(crate) const ARRAY_ALIGNMENT: usize = 64;

pub(crate) fn align_output(target: &mut Vec<u8>) {
    let aligned = target.len().div_ceil(ARRAY_ALIGNMENT) * ARRAY_ALIGNMENT;
    target.resize(aligned, 0);
}

pub(crate) fn align_input(cursor: &mut Cursor<&[u8]>) -> Result<()> {
    let position = cursor.position() as usize;
    let aligned = position.div_ceil(ARRAY_ALIGNMENT) * ARRAY_ALIGNMENT;
    if aligned > cursor.get_ref().len() {
        return Err(CntsError::InvalidData(
            "aligned array offset exceeds payload length".to_string(),
        ));
    }
    if cursor.get_ref()[position..aligned]
        .iter()
        .any(|byte| *byte != 0)
    {
        return Err(CntsError::InvalidData(
            "array alignment padding contains non-zero bytes".to_string(),
        ));
    }
    cursor.set_position(aligned as u64);
    Ok(())
}

pub(crate) fn put_u8(target: &mut Vec<u8>, value: u8) {
    target.push(value);
}

pub(crate) fn put_u16(target: &mut Vec<u8>, value: u16) {
    target.extend_from_slice(&value.to_le_bytes());
}

pub(crate) fn put_u32(target: &mut Vec<u8>, value: u32) {
    target.extend_from_slice(&value.to_le_bytes());
}

pub(crate) fn put_i32(target: &mut Vec<u8>, value: i32) {
    target.extend_from_slice(&value.to_le_bytes());
}

pub(crate) fn put_u64(target: &mut Vec<u8>, value: u64) {
    target.extend_from_slice(&value.to_le_bytes());
}

pub(crate) fn put_i64(target: &mut Vec<u8>, value: i64) {
    target.extend_from_slice(&value.to_le_bytes());
}

pub(crate) fn put_i128(target: &mut Vec<u8>, value: i128) {
    target.extend_from_slice(&value.to_le_bytes());
}

pub(crate) fn put_bytes(target: &mut Vec<u8>, value: &[u8]) {
    put_u64(target, value.len() as u64);
    target.extend_from_slice(value);
}

pub(crate) fn put_string(target: &mut Vec<u8>, value: &str) {
    put_u32(target, value.len() as u32);
    target.extend_from_slice(value.as_bytes());
}

pub(crate) fn read_exact<const N: usize>(cursor: &mut Cursor<&[u8]>) -> Result<[u8; N]> {
    let mut bytes = [0_u8; N];
    cursor.read_exact(&mut bytes)?;
    Ok(bytes)
}

pub(crate) fn read_u8(cursor: &mut Cursor<&[u8]>) -> Result<u8> {
    Ok(read_exact::<1>(cursor)?[0])
}

pub(crate) fn read_u16(cursor: &mut Cursor<&[u8]>) -> Result<u16> {
    Ok(u16::from_le_bytes(read_exact::<2>(cursor)?))
}

pub(crate) fn read_u32(cursor: &mut Cursor<&[u8]>) -> Result<u32> {
    Ok(u32::from_le_bytes(read_exact::<4>(cursor)?))
}

pub(crate) fn read_i32(cursor: &mut Cursor<&[u8]>) -> Result<i32> {
    Ok(i32::from_le_bytes(read_exact::<4>(cursor)?))
}

pub(crate) fn read_u64(cursor: &mut Cursor<&[u8]>) -> Result<u64> {
    Ok(u64::from_le_bytes(read_exact::<8>(cursor)?))
}

pub(crate) fn read_i64(cursor: &mut Cursor<&[u8]>) -> Result<i64> {
    Ok(i64::from_le_bytes(read_exact::<8>(cursor)?))
}

pub(crate) fn read_i128(cursor: &mut Cursor<&[u8]>) -> Result<i128> {
    Ok(i128::from_le_bytes(read_exact::<16>(cursor)?))
}

pub(crate) fn read_bytes(cursor: &mut Cursor<&[u8]>) -> Result<Vec<u8>> {
    let length = read_u64(cursor)? as usize;
    let remaining = cursor
        .get_ref()
        .len()
        .saturating_sub(cursor.position() as usize);
    if length > remaining {
        return Err(CntsError::InvalidData(format!(
            "byte sequence length {length} exceeds remaining payload {remaining}"
        )));
    }
    let mut bytes = vec![0_u8; length];
    cursor.read_exact(&mut bytes)?;
    Ok(bytes)
}

pub(crate) fn read_string(cursor: &mut Cursor<&[u8]>) -> Result<String> {
    let length = read_u32(cursor)? as usize;
    let remaining = cursor
        .get_ref()
        .len()
        .saturating_sub(cursor.position() as usize);
    if length > remaining {
        return Err(CntsError::InvalidData(format!(
            "string length {length} exceeds remaining payload {remaining}"
        )));
    }
    let mut bytes = vec![0_u8; length];
    cursor.read_exact(&mut bytes)?;
    String::from_utf8(bytes).map_err(|error| CntsError::InvalidData(error.to_string()))
}

pub(crate) fn ensure_consumed(cursor: &Cursor<&[u8]>) -> Result<()> {
    let consumed = cursor.position() as usize;
    let total = cursor.get_ref().len();
    if consumed != total {
        return Err(CntsError::InvalidData(format!(
            "payload contains {} trailing bytes",
            total - consumed
        )));
    }
    Ok(())
}
