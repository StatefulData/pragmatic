"""
sf_parquet_harness_v3/src/schema.py

Canonical PyArrow schema for the v3 test dataset.
Every field annotated with source Snowflake type in metadata.
"""
from __future__ import annotations

import pyarrow as pa


def _f(name: str, arrow_type: pa.DataType, sf_type: str, nullable: bool = True) -> pa.Field:
    """Create a pa.Field with Snowflake source type metadata."""
    return pa.field(name, arrow_type, nullable=nullable,
                    metadata={"sf_type": sf_type})


# ---------------------------------------------------------------------------
# Named struct schemas (used both standalone and inside arrays)
# ---------------------------------------------------------------------------
USER_STRUCT = pa.struct([
    pa.field("user_id",     pa.int32()),
    pa.field("username",    pa.string()),
    pa.field("score",       pa.decimal128(10, 4)),
    pa.field("is_active",   pa.bool_()),
    pa.field("signup_date", pa.string()),   # DATE serialized as ISO string in VARIANT
])

GEO_STRUCT = pa.struct([
    pa.field("lat",        pa.decimal128(8, 6)),
    pa.field("lon",        pa.decimal128(9, 6)),
    pa.field("altitude_m", pa.int32()),
    pa.field("label",      pa.string()),
])

EVENT_STRUCT = pa.struct([
    pa.field("event_id", pa.int32()),
    pa.field("event_ts", pa.string()),      # TIMESTAMP serialized as ISO string in VARIANT
    pa.field("value",    pa.decimal128(12, 4)),
])


# ---------------------------------------------------------------------------
# Approach A schema — used when converting Snowflake Arrow batches
# Approach B schema — same Arrow types; Approach B builds column-by-column
# They share one definition; the conversion path differs, not the schema.
# ---------------------------------------------------------------------------
SCHEMA: pa.Schema = pa.schema([
    # --- metadata ---
    _f("row_id",       pa.int64(),                   "INTEGER"),
    _f("is_null_row",  pa.bool_(),                   "BOOLEAN", nullable=False),

    # --- primitives ---
    _f("col_bool",     pa.bool_(),                   "BOOLEAN"),
    _f("col_int16",    pa.int16(),                   "SMALLINT"),
    _f("col_int32",    pa.int32(),                   "INTEGER"),
    _f("col_int64",    pa.int64(),                   "BIGINT"),
    _f("col_decimal_10_4",  pa.decimal128(10, 4),   "NUMBER(10,4)"),
    _f("col_decimal_38_12", pa.decimal128(38, 12),  "NUMBER(38,12)"),
    _f("col_double",   pa.float64(),                 "DOUBLE"),
    _f("col_float",    pa.float64(),                 "FLOAT"),
    _f("col_varchar",  pa.string(),                  "VARCHAR(64)"),
    _f("col_text",     pa.large_string(),            "TEXT"),

    # --- date / time / timestamp ---
    _f("col_date",     pa.date32(),                  "DATE"),
    _f("col_time_us",  pa.time64("us"),              "TIME(6)"),
    _f("col_ts_ms",    pa.timestamp("ms"),            "TIMESTAMP_NTZ(3)"),
    _f("col_ts_us",    pa.timestamp("us"),            "TIMESTAMP_NTZ(6)"),
    _f("col_ts_ns",    pa.timestamp("ns"),            "TIMESTAMP_NTZ(9)"),
    _f("col_ts_ltz",   pa.timestamp("us", tz="UTC"), "TIMESTAMP_LTZ(6)"),

    # --- binary ---
    _f("col_binary",   pa.binary(),                  "BINARY(4)"),

    # --- variant → JSON string ---
    _f("col_variant",        pa.string(),            "VARIANT"),
    _f("col_variant_array",  pa.string(),            "VARIANT"),

    # --- named structs (stored as pa.struct) ---
    _f("col_struct_user",    USER_STRUCT,             "OBJECT(user)"),
    _f("col_struct_geo",     GEO_STRUCT,              "OBJECT(geo)"),

    # --- typed arrays ---
    _f("col_arr_int",        pa.list_(pa.int64()),           "ARRAY(INTEGER)"),
    _f("col_arr_double",     pa.list_(pa.float64()),         "ARRAY(DOUBLE)"),
    _f("col_arr_decimal",    pa.list_(pa.decimal128(10, 4)), "ARRAY(NUMBER(10,4))"),
    _f("col_arr_date",       pa.list_(pa.date32()),          "ARRAY(DATE)"),
    _f("col_arr_ts",         pa.list_(pa.timestamp("us")),   "ARRAY(TIMESTAMP_NTZ(6))"),
    _f("col_arr_bool",       pa.list_(pa.bool_()),           "ARRAY(BOOLEAN)"),
    _f("col_arr_string",     pa.list_(pa.string()),          "ARRAY(VARCHAR)"),

    # --- array of struct ---
    _f("col_arr_struct",     pa.list_(EVENT_STRUCT),         "ARRAY(OBJECT(event))"),

    # --- map / dict variants ---
    _f("col_map_str_int",     pa.map_(pa.string(), pa.int64()),            "MAP(STRING,INTEGER)"),
    _f("col_map_str_double",  pa.map_(pa.string(), pa.float64()),          "MAP(STRING,DOUBLE)"),
    _f("col_map_str_string",  pa.map_(pa.string(), pa.string()),           "MAP(STRING,STRING)"),
    _f("col_map_str_bool",    pa.map_(pa.string(), pa.bool_()),            "MAP(STRING,BOOLEAN)"),
    _f("col_map_str_decimal", pa.map_(pa.string(), pa.decimal128(12, 4)), "MAP(STRING,DECIMAL(12,4))"),

    # --- always-null columns ---
    _f("col_always_null_int",     pa.int64(),   "INTEGER"),
    _f("col_always_null_str",     pa.string(),  "VARCHAR"),
    _f("col_always_null_ts",      pa.timestamp("us"), "TIMESTAMP_NTZ(6)"),
    _f("col_always_null_variant", pa.string(),  "VARIANT"),
    _f("col_always_null_bool",    pa.bool_(),   "BOOLEAN"),
], metadata={
    "source":  "snowflake",
    "harness": "v3",
})

# Column names grouped by category (for targeted test assertions)
PRIMITIVE_COLS     = ["col_bool", "col_int16", "col_int32", "col_int64",
                      "col_decimal_10_4", "col_decimal_38_12",
                      "col_double", "col_float", "col_varchar", "col_text"]
DATETIME_COLS      = ["col_date", "col_time_us", "col_ts_ms", "col_ts_us",
                      "col_ts_ns", "col_ts_ltz"]
BINARY_COLS        = ["col_binary"]
VARIANT_COLS       = ["col_variant", "col_variant_array"]
STRUCT_COLS        = ["col_struct_user", "col_struct_geo"]
ARRAY_COLS         = ["col_arr_int", "col_arr_double", "col_arr_decimal",
                      "col_arr_date", "col_arr_ts", "col_arr_bool",
                      "col_arr_string", "col_arr_struct"]
MAP_COLS           = ["col_map_str_int", "col_map_str_double",
                      "col_map_str_string", "col_map_str_bool",
                      "col_map_str_decimal"]
ALWAYS_NULL_COLS   = ["col_always_null_int", "col_always_null_str",
                      "col_always_null_ts",  "col_always_null_variant",
                      "col_always_null_bool"]
