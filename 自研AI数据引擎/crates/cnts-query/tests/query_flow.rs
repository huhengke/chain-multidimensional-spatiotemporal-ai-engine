use std::{
    fs::{self, OpenOptions},
    io::{Seek, SeekFrom, Write},
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

use cnts_format::{read_current_generation, verify_generation, MergeMode, QueryPlan};

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap()
}

fn read_plan(name: &str) -> QueryPlan {
    let path = workspace_root().join("tests/fixtures").join(name);
    serde_json::from_reader(fs::File::open(path).unwrap()).unwrap()
}

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
fn build_query_corruption_and_rollback_flow() {
    let workspace = workspace_root();
    let temp = temporary_directory("query-flow");
    let root = temp.join("data");
    let first = cnts_builder::build_generation(
        &workspace.join("schemas/demo-sales.json"),
        &workspace.join("tests/fixtures/demo-sales.jsonl"),
        &root,
    )
    .unwrap();
    assert_eq!(first.row_count, 12);
    assert_eq!(first.node_count, 10);

    let total = cnts_query::query(&root, &read_plan("query-total.json")).unwrap();
    assert!(!total.detail_fallback);
    assert_eq!(total.rows.len(), 1);
    assert_eq!(total.rows[0].record_count, 12);
    assert_eq!(total.rows[0].metrics["sales_amount"], "2220.00");
    assert_eq!(total.rows[0].metrics["sales_volume"], "222.000");
    assert_eq!(total.route.last().unwrap().operation, "READ_STORE");

    let grouped = cnts_query::query(&root, &read_plan("query-company-category.json")).unwrap();
    assert!(grouped.detail_fallback);
    assert_eq!(grouped.rows.len(), 2);
    assert_eq!(grouped.rows[0].dimensions["company"], "示例区域丁");
    assert_eq!(grouped.rows[0].metrics["sales_amount"], "480.00");
    assert_eq!(grouped.rows[1].dimensions["company"], "示例区域甲");
    assert_eq!(grouped.rows[1].metrics["sales_amount"], "330.00");
    assert!(grouped
        .route
        .iter()
        .any(|step| step.operation == "INTERSECT_OUTER"));

    let common = cnts_query::query(&root, &read_plan("query-company-common.json")).unwrap();
    assert!(!common.detail_fallback);
    assert_eq!(common.rows.len(), 1);
    assert_eq!(common.rows[0].record_count, 6);
    assert_eq!(common.rows[0].metrics["sales_amount"], "960.00");
    assert_eq!(common.rows[0].metrics["sales_volume"], "96.000");
    assert!(common
        .route
        .iter()
        .any(|step| step.operation == "DESCEND_COMMON"));
    assert_eq!(common.route.last().unwrap().operation, "READ_STORE");

    let mut grouped_time = read_plan("query-total.json");
    grouped_time.group_by = vec!["day".to_string(), "company".to_string()];
    let grouped_time_result = cnts_query::query(&root, &grouped_time).unwrap();
    assert!(grouped_time_result.detail_fallback);
    assert_eq!(grouped_time_result.rows.len(), 6);
    assert_eq!(grouped_time_result.rows[0].dimensions["day"], "2026-08-01");
    assert_eq!(grouped_time_result.rows[0].dimensions["company"], "示例区域丁");
    assert_eq!(
        grouped_time_result.rows[0].metrics["sales_amount"],
        "400.00"
    );

    let mut grouped_month = read_plan("query-total.json");
    grouped_month.group_by = vec!["month".to_string()];
    let grouped_month_result = cnts_query::query(&root, &grouped_month).unwrap();
    assert_eq!(grouped_month_result.rows.len(), 1);
    assert_eq!(grouped_month_result.rows[0].dimensions["month"], "202608");
    assert_eq!(
        grouped_month_result.rows[0].metrics["sales_amount"],
        "2220.00"
    );

    let mut grouped_foundation_time = read_plan("query-total.json");
    grouped_foundation_time.group_by = vec![
        "season".to_string(),
        "half_year".to_string(),
        "month_parity".to_string(),
        "weekday_type".to_string(),
    ];
    let grouped_foundation_result = cnts_query::query(&root, &grouped_foundation_time).unwrap();
    assert!(!grouped_foundation_result.rows.is_empty());
    for row in grouped_foundation_result.rows {
        assert!(row.dimensions.contains_key("season"));
        assert!(row.dimensions.contains_key("half_year"));
        assert!(row.dimensions.contains_key("month_parity"));
        assert!(row.dimensions.contains_key("weekday_type"));
    }

    let mut empty = read_plan("query-total.json");
    empty.time_range.start = "2027-01-01".to_string();
    empty.time_range.end = "2027-01-31".to_string();
    let empty_result = cnts_query::query(&root, &empty).unwrap();
    assert!(empty_result.rows.is_empty());
    assert!(!empty_result.detail_fallback);
    assert_eq!(empty_result.route.last().unwrap().matched, 0);

    let mut unsupported_merge = read_plan("query-total.json");
    unsupported_merge.merge_mode = MergeMode::SumTotal;
    assert!(cnts_query::query(&root, &unsupported_merge).is_err());

    let second = cnts_builder::build_generation(
        &workspace.join("schemas/demo-sales.json"),
        &workspace.join("tests/fixtures/demo-sales-v2.jsonl"),
        &root,
    )
    .unwrap();
    assert_ne!(first.generation_id, second.generation_id);
    let pinned_first =
        cnts_query::query_generation(&root, &first.generation_id, &read_plan("query-total.json"))
            .unwrap();
    assert_eq!(pinned_first.generation_id, first.generation_id);
    assert_eq!(pinned_first.rows[0].metrics["sales_amount"], "2220.00");
    assert!(
        cnts_query::query_generation(&root, "../CURRENT", &read_plan("query-total.json")).is_err()
    );
    let rollback_target = cnts_builder::rollback(&root).unwrap();
    assert_eq!(rollback_target, first.generation_id);
    assert_eq!(
        fs::read_to_string(root.join("CURRENT")).unwrap().trim(),
        first.generation_id
    );

    let generation_dir = read_current_generation(&root).unwrap();
    let inner_path = generation_dir.join("inner.cnts");
    let mut file = OpenOptions::new().write(true).open(&inner_path).unwrap();
    file.seek(SeekFrom::End(-1)).unwrap();
    file.write_all(&[0xff]).unwrap();
    file.sync_all().unwrap();
    drop(file);
    assert!(verify_generation(&generation_dir).is_err());
    fs::remove_dir_all(temp).unwrap();
}
