"""
sf_parquet_harness_v3/src/writer_approach_a.py

Approach A: Arrow-native (zero-copy / batch cast path)

In production with a real Snowflake connection:
    cursor.execute(sql)
    batches = cursor.fetch_arrow_batches()   # returns Iterator[pa.RecordBatch]
    table   = pa.Table.from_batches(batches)
    # then cast to target schema

For local tests (no Snowflake), we simulate the "raw" Arrow table that the
connector would return — i.e., complex types arrive as JSON strings — then
apply the same cast pipeline.

KEY DIFFERENCE FROM APPROACH B:
  - VARIANT, OBJECT, ARRAY columns arrive as plain strings (JSON) in Approach A
    (Snowflake connector uses pa.string() for these in its Arrow output)
  - Numeric columns may arrive as pa.int64 / pa.float64 already — no per-row Python conversion
  - We use pa.compute / pa.cast where possible; fall back to Python only for complex types

CAVEATS documented in CLAUDE.md:
  - Snowflake inferred schema ≠ target schema; always cast explicitly
  - VARIANT/OBJECT/ARRAY come as JSON strings even in Arrow path
  - DECIMAL may come as pa.string() from connector; cast via pa.cast to decimal128
  - TIMESTAMP comes as pa.int64 (epoch at connector precision); cast via pa.cast
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
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


def _simulate_snowflake_arrow_table(rows: list[dict]) -> pa.Table:
    """
    Simulate what the Snowflake Python connector returns via fetch_arrow_batches().

    The connector returns:
      - Numeric columns: pa.int64 / pa.float64 (already numeric)
      - DATE: pa.int32 (days since epoch)
      - TIME: pa.int64 (microseconds since midnight)
      - TIMESTAMP_NTZ: pa.int64 (epoch at connector precision)
      - BINARY: pa.binary()
      - BOOLEAN: pa.bool_()
      - VARIANT/OBJECT/ARRAY: pa.string() (JSON serialized by connector)
      - VARCHAR: pa.string()

    For the simulation, we pre-serialize complex types to JSON strings,
    and express timestamps/dates as integers.
    """
    from .converters import to_date32, to_ts_us, to_ts_ns, to_ts_ms

    n = len(rows)

    def col(name): return [r[name] for r in rows]

    # Complex types → JSON string (what connector does in Arrow mode)
    def _to_json(v):
        if v is None: return None
        if isinstance(v, (dict, list)):
            return json.dumps(v, default=str)
        return json.dumps(v)

    # Build "raw" table mimicking connector Arrow output
    raw = pa.table({
        "row_id":       pa.array(col("row_id"),      type=pa.int64()),
        "is_null_row":  pa.array(col("is_null_row"), type=pa.bool_()),

        "col_bool":     pa.array(col("col_bool"),    type=pa.bool_()),
        "col_int16":    pa.array(col("col_int16"),   type=pa.int64()),  # connector uses int64
        "col_int32":    pa.array(col("col_int32"),   type=pa.int64()),
        "col_int64":    pa.array(col("col_int64"),   type=pa.int64()),

        # Connector returns DECIMAL as string in Arrow mode
        "col_decimal_10_4":  pa.array([str(v) if v is not None else None for v in col("col_decimal_10_4")],  type=pa.string()),
        "col_decimal_38_12": pa.array([str(v) if v is not None else None for v in col("col_decimal_38_12")], type=pa.string()),

        "col_double":   pa.array(col("col_double"),  type=pa.float64()),
        "col_float":    pa.array(col("col_float"),   type=pa.float64()),
        "col_varchar":  pa.array(col("col_varchar"), type=pa.string()),
        "col_text":     pa.array(col("col_text"),    type=pa.large_string()),

        # DATE as int32 (days since epoch)
        "col_date":     pa.array([to_date32(v) for v in col("col_date")], type=pa.int32()),
        # TIME as int64 (us since midnight)
        "col_time_us":  pa.array(col("col_time_us"), type=pa.int64()),
        # TIMESTAMP as int64
        "col_ts_ms":    pa.array([to_ts_ms(v) for v in col("col_ts_ms")],  type=pa.int64()),
        "col_ts_us":    pa.array([to_ts_us(v) for v in col("col_ts_us")],  type=pa.int64()),
        "col_ts_ns":    pa.array([to_ts_ns(v) for v in col("col_ts_ns")],  type=pa.int64()),
        "col_ts_ltz":   pa.array([to_ts_us(v) for v in col("col_ts_ltz")], type=pa.int64()),

        "col_binary":   pa.array(col("col_binary"),  type=pa.binary()),

        # VARIANT/OBJECT/ARRAY → JSON string
        "col_variant":        pa.array([_to_json(v) for v in col("col_variant")],        type=pa.string()),
        "col_variant_array":  pa.array([_to_json(v) for v in col("col_variant_array")],  type=pa.string()),
        "col_struct_user":    pa.array([_to_json(v) for v in col("col_struct_user")],    type=pa.string()),
        "col_struct_geo":     pa.array([_to_json(v) for v in col("col_struct_geo")],     type=pa.string()),
        "col_arr_int":        pa.array([_to_json(v) for v in col("col_arr_int")],        type=pa.string()),
        "col_arr_double":     pa.array([_to_json(v) for v in col("col_arr_double")],     type=pa.string()),
        "col_arr_decimal":    pa.array([_to_json(v) for v in col("col_arr_decimal")],    type=pa.string()),
        "col_arr_date":       pa.array([_to_json(v) for v in col("col_arr_date")],       type=pa.string()),
        "col_arr_ts":         pa.array([_to_json(v) for v in col("col_arr_ts")],         type=pa.string()),
        "col_arr_bool":       pa.array([_to_json(v) for v in col("col_arr_bool")],       type=pa.string()),
        "col_arr_string":     pa.array([_to_json(v) for v in col("col_arr_string")],     type=pa.string()),
        "col_arr_struct":     pa.array([_to_json(v) for v in col("col_arr_struct")],     type=pa.string()),
        "col_map_str_int":    pa.array([_to_json(v) for v in col("col_map_str_int")],    type=pa.string()),
        "col_map_str_double": pa.array([_to_json(v) for v in col("col_map_str_double")], type=pa.string()),
        "col_map_str_string": pa.array([_to_json(v) for v in col("col_map_str_string")], type=pa.string()),
        "col_map_str_bool":   pa.array([_to_json(v) for v in col("col_map_str_bool")],   type=pa.string()),
        "col_map_str_decimal":pa.array([_to_json(v) for v in col("col_map_str_decimal")],type=pa.string()),

        "col_always_null_int":     pa.array([None]*n, type=pa.int64()),
        "col_always_null_str":     pa.array([None]*n, type=pa.string()),
        "col_always_null_ts":      pa.array([None]*n, type=pa.int64()),
        "col_always_null_variant": pa.array([None]*n, type=pa.string()),
        "col_always_null_bool":    pa.array([None]*n, type=pa.bool_()),
    })
    return raw


def _cast_raw_to_target(raw: pa.Table) -> pa.Table:
    """
    Cast the raw Snowflake Arrow table to the target schema.

    Arrow-native casts (via pc.cast) are used wherever possible.
    Python-level parsing is required only for VARIANT/OBJECT/ARRAY (JSON strings).
    """
    def get(name): return raw.column(name).to_pylist()

    cols: dict[str, pa.Array] = {}

    # --- direct Arrow casts (fast path) ---
    cols["row_id"]      = raw.column("row_id")
    cols["is_null_row"] = raw.column("is_null_row")
    cols["col_bool"]    = raw.column("col_bool")

    # int64 → smaller int types via cast
    cols["col_int16"]   = pc.cast(raw.column("col_int16"), pa.int16())
    cols["col_int32"]   = pc.cast(raw.column("col_int32"), pa.int32())
    cols["col_int64"]   = raw.column("col_int64")

    # Decimal: string → decimal128 via Python (pa.cast from string not always available)
    cols["col_decimal_10_4"]  = pa.array(
        [Decimal(v) if v is not None else None for v in get("col_decimal_10_4")],
        type=pa.decimal128(10, 4))
    cols["col_decimal_38_12"] = pa.array(
        [Decimal(v) if v is not None else None for v in get("col_decimal_38_12")],
        type=pa.decimal128(38, 12))

    cols["col_double"]  = raw.column("col_double")
    cols["col_float"]   = raw.column("col_float")
    cols["col_varchar"] = raw.column("col_varchar")
    cols["col_text"]    = raw.column("col_text")

    # Temporal: int64 → timestamp via cast
    cols["col_date"]    = pc.cast(raw.column("col_date"),    pa.date32())
    cols["col_time_us"] = pc.cast(raw.column("col_time_us"), pa.time64("us"))
    cols["col_ts_ms"]   = pc.cast(raw.column("col_ts_ms"),   pa.timestamp("ms"))
    cols["col_ts_us"]   = pc.cast(raw.column("col_ts_us"),   pa.timestamp("us"))
    cols["col_ts_ns"]   = pc.cast(raw.column("col_ts_ns"),   pa.timestamp("ns"))
    cols["col_ts_ltz"]  = pc.cast(raw.column("col_ts_ltz"),  pa.timestamp("us", tz="UTC"))

    cols["col_binary"]  = raw.column("col_binary")

    # VARIANT → keep as string (JSON)
    cols["col_variant"]       = raw.column("col_variant")
    cols["col_variant_array"] = raw.column("col_variant_array")

    # Structs: parse JSON string → dict → pa.array with pa.struct type
    cols["col_struct_user"] = pa.array(
        [convert_user_struct(json.loads(v)) if v is not None else None
         for v in get("col_struct_user")],
        type=USER_STRUCT)
    cols["col_struct_geo"] = pa.array(
        [convert_geo_struct(json.loads(v)) if v is not None else None
         for v in get("col_struct_geo")],
        type=GEO_STRUCT)

    # Arrays: parse JSON string → list → apply element converters
    def _parse_json_list(col_name):
        return [json.loads(v) if v is not None else None for v in get(col_name)]

    cols["col_arr_int"] = pa.array(
        [convert_arr_int(v) for v in _parse_json_list("col_arr_int")],
        type=pa.list_(pa.int64()))
    cols["col_arr_double"] = pa.array(
        [convert_arr_double(v) for v in _parse_json_list("col_arr_double")],
        type=pa.list_(pa.float64()))
    cols["col_arr_decimal"] = pa.array(
        [convert_arr_decimal(v, 10, 4) for v in _parse_json_list("col_arr_decimal")],
        type=pa.list_(pa.decimal128(10, 4)))
    cols["col_arr_date"] = pa.array(
        [convert_arr_date(v) for v in _parse_json_list("col_arr_date")],
        type=pa.list_(pa.date32()))
    cols["col_arr_ts"] = pa.array(
        [convert_arr_ts(v) for v in _parse_json_list("col_arr_ts")],
        type=pa.list_(pa.timestamp("us")))
    cols["col_arr_bool"] = pa.array(
        [convert_arr_bool(v) for v in _parse_json_list("col_arr_bool")],
        type=pa.list_(pa.bool_()))
    cols["col_arr_string"] = pa.array(
        [convert_arr_string(v) for v in _parse_json_list("col_arr_string")],
        type=pa.list_(pa.string()))
    cols["col_arr_struct"] = pa.array(
        [convert_arr_struct(v, convert_event_struct) for v in _parse_json_list("col_arr_struct")],
        type=pa.list_(EVENT_STRUCT))

    # Maps: parse JSON string → dict → list of tuples
    def _parse_map(col_name, converter):
        return pa.array(
            [converter(json.loads(v)) if v is not None else None
             for v in get(col_name)],
            type=None)  # type set below

    cols["col_map_str_int"] = pa.array(
        [convert_map_str_int(json.loads(v)) if v is not None else None for v in get("col_map_str_int")],
        type=pa.map_(pa.string(), pa.int64()))
    cols["col_map_str_double"] = pa.array(
        [convert_map_str_double(json.loads(v)) if v is not None else None for v in get("col_map_str_double")],
        type=pa.map_(pa.string(), pa.float64()))
    cols["col_map_str_string"] = pa.array(
        [convert_map_str_string(json.loads(v)) if v is not None else None for v in get("col_map_str_string")],
        type=pa.map_(pa.string(), pa.string()))
    cols["col_map_str_bool"] = pa.array(
        [convert_map_str_bool(json.loads(v)) if v is not None else None for v in get("col_map_str_bool")],
        type=pa.map_(pa.string(), pa.bool_()))
    cols["col_map_str_decimal"] = pa.array(
        [convert_map_str_decimal(json.loads(v), 12, 4) if v is not None else None for v in get("col_map_str_decimal")],
        type=pa.map_(pa.string(), pa.decimal128(12, 4)))

    # Always-null
    n_rows = len(raw)
    cols["col_always_null_int"]     = pa.array([None]*n_rows, type=pa.int64())
    cols["col_always_null_str"]     = pa.array([None]*n_rows, type=pa.string())
    cols["col_always_null_ts"]      = pa.array([None]*n_rows, type=pa.timestamp("us"))
    cols["col_always_null_variant"] = pa.array([None]*n_rows, type=pa.string())
    cols["col_always_null_bool"]    = pa.array([None]*n_rows, type=pa.bool_())

    return pa.table(
        {name: cols[name] for name in [f.name for f in SCHEMA]},
        schema=SCHEMA,
    )


def write_parquet_approach_a(rows: list[dict], path: str,
                              compression: str = "snappy") -> dict:
    """
    Write rows to Parquet using Approach A (Arrow-native batch cast path).
    Simulates what you'd do with real Snowflake Arrow batches.
    Returns timing stats.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    raw = _simulate_snowflake_arrow_table(rows)
    t_sim = time.perf_counter() - t0

    t1 = time.perf_counter()
    table = _cast_raw_to_target(raw)
    t_cast = time.perf_counter() - t1

    t2 = time.perf_counter()
    pq.write_table(table, str(path), compression=compression,
                   write_statistics=True, use_deprecated_int96_timestamps=False)
    t_write = time.perf_counter() - t2

    return {
        "approach": "A",
        "n_rows": len(rows),
        "n_cols": len(table.schema),
        "simulate_sec": round(t_sim, 4),
        "cast_sec":     round(t_cast, 4),
        "write_sec":    round(t_write, 4),
        "total_sec":    round(t_sim + t_cast + t_write, 4),
        "file_bytes":   path.stat().st_size,
        "path": str(path),
    }
