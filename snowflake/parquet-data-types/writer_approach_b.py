"""
sf_parquet_harness_v3/src/writer_approach_b.py

Approach B: dict/row path
- Takes a list of Python dicts (from cursor.fetchall(as_dict=True) or generator)
- Applies explicit per-column converters
- Builds pa.arrays column-by-column
- Assembles pa.Table and writes to Parquet

This approach gives fine-grained control over every value transformation.
Slower than Approach A for large datasets but simpler to debug.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from converters import (
    to_date32, to_ts_ms, to_ts_us, to_ts_ns, to_decimal, variant_to_json,
    convert_user_struct, convert_geo_struct, convert_event_struct,
    convert_arr_int, convert_arr_double, convert_arr_decimal,
    convert_arr_date, convert_arr_ts, convert_arr_bool, convert_arr_string,
    convert_arr_struct, convert_map_str_int, convert_map_str_double,
    convert_map_str_string, convert_map_str_bool, convert_map_str_decimal,
)
from schema import SCHEMA, USER_STRUCT, GEO_STRUCT, EVENT_STRUCT


def _col(rows: list[dict], name: str) -> list:
    return [r[name] for r in rows]


def _json_col(rows: list[dict], name: str) -> list:
    """Like _col, but auto-parses JSON strings (Snowflake DictCursor returns complex types as strings)."""
    out = []
    for r in rows:
        v = r[name]
        if isinstance(v, str):
            v = json.loads(v)
        out.append(v)
    return out


def _build_table(rows: list[dict]) -> pa.Table:
    """Convert list of raw dicts → pa.Table using Approach B (column-by-column)."""

    def c(name): return _col(rows, name)
    def jc(name): return _json_col(rows, name)

    arrays: dict[str, pa.Array] = {}

    # --- metadata ---
    arrays["row_id"]      = pa.array(c("row_id"),      type=pa.int64())
    arrays["is_null_row"] = pa.array(c("is_null_row"), type=pa.bool_())

    # --- primitives ---
    arrays["col_bool"]    = pa.array(c("col_bool"),    type=pa.bool_())
    arrays["col_int16"]   = pa.array(c("col_int16"),   type=pa.int16())
    arrays["col_int32"]   = pa.array(c("col_int32"),   type=pa.int32())
    arrays["col_int64"]   = pa.array(c("col_int64"),   type=pa.int64())

    arrays["col_decimal_10_4"]  = pa.array(
        [to_decimal(v, 10, 4) for v in c("col_decimal_10_4")],
        type=pa.decimal128(10, 4))
    arrays["col_decimal_38_12"] = pa.array(
        [to_decimal(v, 38, 12) for v in c("col_decimal_38_12")],
        type=pa.decimal128(38, 12))

    arrays["col_double"]  = pa.array(c("col_double"),  type=pa.float64())
    arrays["col_float"]   = pa.array(c("col_float"),   type=pa.float64())
    arrays["col_varchar"] = pa.array(c("col_varchar"), type=pa.string())
    arrays["col_text"]    = pa.array(c("col_text"),    type=pa.large_string())

    # --- date / time / timestamp ---
    arrays["col_date"] = pa.array(
        [to_date32(v) for v in c("col_date")], type=pa.date32())

    arrays["col_time_us"] = pa.array(c("col_time_us"), type=pa.time64("us"))

    arrays["col_ts_ms"]  = pa.array(
        [to_ts_ms(v)  for v in c("col_ts_ms")],  type=pa.timestamp("ms"))
    arrays["col_ts_us"]  = pa.array(
        [to_ts_us(v)  for v in c("col_ts_us")],  type=pa.timestamp("us"))
    arrays["col_ts_ns"]  = pa.array(
        [to_ts_ns(v)  for v in c("col_ts_ns")],  type=pa.timestamp("ns"))
    arrays["col_ts_ltz"] = pa.array(
        [to_ts_us(v)  for v in c("col_ts_ltz")], type=pa.timestamp("us", tz="UTC"))

    # --- binary ---
    arrays["col_binary"] = pa.array(c("col_binary"), type=pa.binary())

    # --- variant ---
    arrays["col_variant"]       = pa.array(
        [variant_to_json(v) for v in c("col_variant")], type=pa.string())
    arrays["col_variant_array"] = pa.array(
        [variant_to_json(v) for v in c("col_variant_array")], type=pa.string())

    # --- named structs ---
    arrays["col_struct_user"] = pa.array(
        [convert_user_struct(v) for v in jc("col_struct_user")],
        type=USER_STRUCT)
    arrays["col_struct_geo"] = pa.array(
        [convert_geo_struct(v)  for v in jc("col_struct_geo")],
        type=GEO_STRUCT)

    # --- typed arrays ---
    arrays["col_arr_int"] = pa.array(
        [convert_arr_int(v) for v in jc("col_arr_int")],
        type=pa.list_(pa.int64()))
    arrays["col_arr_double"] = pa.array(
        [convert_arr_double(v) for v in jc("col_arr_double")],
        type=pa.list_(pa.float64()))
    arrays["col_arr_decimal"] = pa.array(
        [convert_arr_decimal(v, 10, 4) for v in jc("col_arr_decimal")],
        type=pa.list_(pa.decimal128(10, 4)))
    arrays["col_arr_date"] = pa.array(
        [convert_arr_date(v) for v in jc("col_arr_date")],
        type=pa.list_(pa.date32()))
    arrays["col_arr_ts"] = pa.array(
        [convert_arr_ts(v) for v in jc("col_arr_ts")],
        type=pa.list_(pa.timestamp("us")))
    arrays["col_arr_bool"] = pa.array(
        [convert_arr_bool(v) for v in jc("col_arr_bool")],
        type=pa.list_(pa.bool_()))
    arrays["col_arr_string"] = pa.array(
        [convert_arr_string(v) for v in jc("col_arr_string")],
        type=pa.list_(pa.string()))

    # --- array of struct ---
    arrays["col_arr_struct"] = pa.array(
        [convert_arr_struct(v, convert_event_struct) for v in jc("col_arr_struct")],
        type=pa.list_(EVENT_STRUCT))

    # --- maps ---
    arrays["col_map_str_int"] = pa.array(
        [convert_map_str_int(v) for v in jc("col_map_str_int")],
        type=pa.map_(pa.string(), pa.int64()))
    arrays["col_map_str_double"] = pa.array(
        [convert_map_str_double(v) for v in jc("col_map_str_double")],
        type=pa.map_(pa.string(), pa.float64()))
    arrays["col_map_str_string"] = pa.array(
        [convert_map_str_string(v) for v in jc("col_map_str_string")],
        type=pa.map_(pa.string(), pa.string()))
    arrays["col_map_str_bool"] = pa.array(
        [convert_map_str_bool(v) for v in jc("col_map_str_bool")],
        type=pa.map_(pa.string(), pa.bool_()))
    arrays["col_map_str_decimal"] = pa.array(
        [convert_map_str_decimal(v, 12, 4) for v in jc("col_map_str_decimal")],
        type=pa.map_(pa.string(), pa.decimal128(12, 4)))

    # --- always-null ---
    n_rows = len(rows)
    arrays["col_always_null_int"]     = pa.array([None]*n_rows, type=pa.int64())
    arrays["col_always_null_str"]     = pa.array([None]*n_rows, type=pa.string())
    arrays["col_always_null_ts"]      = pa.array([None]*n_rows, type=pa.timestamp("us"))
    arrays["col_always_null_variant"] = pa.array([None]*n_rows, type=pa.string())
    arrays["col_always_null_bool"]    = pa.array([None]*n_rows, type=pa.bool_())

    # Build table in schema field order
    return pa.table(
        {name: arrays[name] for name in [f.name for f in SCHEMA]},
        schema=SCHEMA,
    )


def write_parquet_approach_b(rows: list[dict], path: str | Path,
                              compression: str = "snappy") -> dict:
    """
    Write rows to Parquet using Approach B (dict/row path).
    Returns timing stats.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    table = _build_table(rows)
    t_build = time.perf_counter() - t0

    t1 = time.perf_counter()
    pq.write_table(table, str(path), compression=compression,
                   write_statistics=True, use_deprecated_int96_timestamps=False)
    t_write = time.perf_counter() - t1

    return {
        "approach": "B",
        "n_rows": len(rows),
        "n_cols": len(table.schema),
        "build_sec": round(t_build, 4),
        "write_sec": round(t_write, 4),
        "total_sec": round(t_build + t_write, 4),
        "file_bytes": path.stat().st_size,
        "path": str(path),
    }
