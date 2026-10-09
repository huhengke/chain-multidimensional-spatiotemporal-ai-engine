use std::{
    fs::{self, OpenOptions},
    io::{Seek, SeekFrom, Write},
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

use cnts_format::{
    write_cnts_file, CntsError, ColumnKind, ColumnValues, DetailColumn, DetailData, DictionarySet,
    FieldDictionary, FileType, InnerColumns, MappedCntsFile, MetricColumn, OuterField, OuterIndex,
    PostingList, StoreData, HEADER_SIZE, NONE_ORDINAL,
};

fn temporary_directory(name: &str) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = std::env::temp_dir().join(format!("cnts-{name}-{}-{nonce}", std::process::id()));
    fs::create_dir_all(&path).unwrap();
    path
}

#[test]
fn all_columnar_sections_round_trip() {
    let dictionaries = DictionarySet {
        fields: vec![FieldDictionary {
            field: "company".to_string(),
            values: vec!["示例区域丁".to_string(), "示例区域甲".to_string()],
        }],
    };
    assert_eq!(
        DictionarySet::decode(&dictionaries.encode().unwrap()).unwrap(),
        dictionaries
    );

    let inner = InnerColumns {
        logical_node_id: vec![[1; 16], [2; 16]],
        parent_ordinal: vec![NONE_ORDINAL, 0],
        first_child_ordinal: vec![1, NONE_ORDINAL],
        next_sibling_ordinal: vec![NONE_ORDINAL; 2],
        axis_id: vec![0, 1],
        value_id: vec![0, 20_000],
        temporal_layer: vec![0, 1],
        branch_depth: vec![0, 0],
        flags: vec![1, 2],
        aggregate_offset: vec![0, 1],
        outer_offset: vec![0, 0],
        detail_start: vec![0, 0],
        detail_count: vec![2, 2],
    };
    assert_eq!(
        InnerColumns::decode(&inner.encode().unwrap()).unwrap(),
        inner
    );

    let store = StoreData {
        node_count: 2,
        metrics: vec![MetricColumn {
            name: "sales_amount".to_string(),
            scale: 2,
            values: vec![3000, 3000],
        }],
    };
    assert_eq!(StoreData::decode(&store.encode().unwrap()).unwrap(), store);

    let outer = OuterIndex {
        fields: vec![OuterField {
            name: "category".to_string(),
            postings: vec![PostingList {
                value_id: 0,
                rows: vec![0, 2, 5, 9],
            }],
        }],
    };
    assert_eq!(OuterIndex::decode(&outer.encode().unwrap()).unwrap(), outer);

    let details = DetailData {
        row_count: 2,
        columns: vec![
            DetailColumn {
                name: "date".to_string(),
                kind: ColumnKind::Time,
                scale: 0,
                null_bitmap: vec![0],
                values: ColumnValues::I32(vec![20_000, 20_001]),
            },
            DetailColumn {
                name: "company".to_string(),
                kind: ColumnKind::Dictionary,
                scale: 0,
                null_bitmap: vec![0],
                values: ColumnValues::U32(vec![1, 0]),
            },
            DetailColumn {
                name: "sales_amount".to_string(),
                kind: ColumnKind::Metric,
                scale: 2,
                null_bitmap: vec![0],
                values: ColumnValues::I64(vec![1000, 2000]),
            },
        ],
    };
    assert_eq!(
        DetailData::decode(&details.encode().unwrap()).unwrap(),
        details
    );
}

#[test]
fn damaged_body_is_rejected() {
    let directory = temporary_directory("damaged-body");
    let path = directory.join("inner.cnts");
    write_cnts_file(
        &path,
        FileType::Inner,
        [1; 16],
        [2; 16],
        1,
        b"verified payload",
    )
    .unwrap();
    MappedCntsFile::open(&path, Some(FileType::Inner)).unwrap();
    let mut file = OpenOptions::new().write(true).open(&path).unwrap();
    file.seek(SeekFrom::End(-1)).unwrap();
    file.write_all(&[0xff]).unwrap();
    file.sync_all().unwrap();
    assert!(MappedCntsFile::open(&path, Some(FileType::Inner)).is_err());
    fs::remove_dir_all(directory).unwrap();
}

fn rewrite_header(path: &std::path::Path, mutate: impl FnOnce(&mut [u8])) {
    let mut bytes = fs::read(path).unwrap();
    mutate(&mut bytes[..HEADER_SIZE]);
    let crc = crc32c::crc32c(&bytes[..HEADER_SIZE - 4]);
    bytes[HEADER_SIZE - 4..HEADER_SIZE].copy_from_slice(&crc.to_le_bytes());
    fs::write(path, bytes).unwrap();
}

#[test]
fn invalid_version_offset_and_truncation_are_rejected() {
    let directory = temporary_directory("invalid-header");

    let version_path = directory.join("version.cnts");
    write_cnts_file(
        &version_path,
        FileType::Inner,
        [1; 16],
        [2; 16],
        1,
        b"payload",
    )
    .unwrap();
    rewrite_header(&version_path, |header| {
        header[4..6].copy_from_slice(&2_u16.to_le_bytes());
    });
    assert!(matches!(
        MappedCntsFile::open(&version_path, Some(FileType::Inner)),
        Err(CntsError::UnsupportedVersion { .. })
    ));

    let offset_path = directory.join("offset.cnts");
    write_cnts_file(
        &offset_path,
        FileType::Inner,
        [1; 16],
        [2; 16],
        1,
        b"payload",
    )
    .unwrap();
    rewrite_header(&offset_path, |header| {
        header[48..56].copy_from_slice(&((HEADER_SIZE + 1) as u64).to_le_bytes());
    });
    assert!(MappedCntsFile::open(&offset_path, Some(FileType::Inner)).is_err());

    let truncated_path = directory.join("truncated.cnts");
    fs::write(&truncated_path, [0_u8; 32]).unwrap();
    assert!(MappedCntsFile::open(&truncated_path, None).is_err());
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn invalid_inner_topology_is_rejected_before_encoding() {
    let mut inner = InnerColumns {
        logical_node_id: vec![[1; 16], [1; 16]],
        parent_ordinal: vec![NONE_ORDINAL, 0],
        first_child_ordinal: vec![1, NONE_ORDINAL],
        next_sibling_ordinal: vec![NONE_ORDINAL; 2],
        axis_id: vec![0, 1],
        value_id: vec![0, 1],
        temporal_layer: vec![0, 1],
        branch_depth: vec![0; 2],
        flags: vec![1, 2],
        aggregate_offset: vec![0, 1],
        outer_offset: vec![0; 2],
        detail_start: vec![0; 2],
        detail_count: vec![1; 2],
    };
    assert!(inner.encode().is_err());
    inner.logical_node_id[1] = [2; 16];
    inner.first_child_ordinal[0] = 99;
    assert!(inner.encode().is_err());
}

#[test]
fn detail_columns_are_split_into_fixed_row_blocks() {
    let row_count = cnts_format::DETAIL_BLOCK_ROWS + 1;
    let details = DetailData {
        row_count: row_count as u64,
        columns: vec![DetailColumn {
            name: "date".to_string(),
            kind: ColumnKind::Time,
            scale: 0,
            null_bitmap: vec![0; row_count.div_ceil(8)],
            values: ColumnValues::I32((0..row_count as i32).collect()),
        }],
    };
    assert_eq!(details.block_count(), 2);
    assert_eq!(
        DetailData::decode(&details.encode().unwrap()).unwrap(),
        details
    );
}

#[test]
fn topology_arrays_start_on_64_byte_boundaries() {
    let inner = InnerColumns {
        logical_node_id: vec![[7; 16]],
        parent_ordinal: vec![NONE_ORDINAL],
        first_child_ordinal: vec![NONE_ORDINAL],
        next_sibling_ordinal: vec![NONE_ORDINAL],
        axis_id: vec![0],
        value_id: vec![0],
        temporal_layer: vec![0],
        branch_depth: vec![0],
        flags: vec![1],
        aggregate_offset: vec![0],
        outer_offset: vec![0],
        detail_start: vec![0],
        detail_count: vec![1],
    };
    let encoded = inner.encode().unwrap();
    assert!(encoded[8..64].iter().all(|byte| *byte == 0));
    assert_eq!(&encoded[64..80], &[7; 16]);
    assert_eq!(InnerColumns::decode(&encoded).unwrap(), inner);
}
