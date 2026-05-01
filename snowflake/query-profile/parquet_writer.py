"""Parquet shard writer.

The Arrow schema here must match the column names and types of
DATA.QUERY_OPERATOR_STATS_HISTORY so that `COPY INTO ... MATCH_BY_COLUMN_NAME`
resolves every column automatically.

Note on OPERATOR_ATTRIBUTES: it's declared VARIANT in Snowflake. We serialize
it as a JSON string in Parquet, and the COPY INTO statement uses PARSE_JSON()
in the SELECT to convert it back to VARIANT on ingest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

# Arrow schema mirrors the DDL in sql/001_create_target_table.sql.
# Column names are lower-cased here because pyarrow prefers that; Snowflake
# COPY INTO with MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE handles the mapping.
ARROW_SCHEMA = pa.schema([
    pa.field("query_id", pa.string(), nullable=False),
    pa.field("query_tag", pa.string()),
    pa.field("start_time", pa.timestamp("ms")),
    pa.field("end_time", pa.timestamp("ms")),
    pa.field("execution_time", pa.int64()),
    pa.field("step_id", pa.int32()),
    pa.field("operator_id", pa.int32()),
    pa.field("parent_operators", pa.list_(pa.int32())),
    pa.field("operator_type", pa.string()),
    # EXECUTION_TIME_BREAKDOWN shreds
    pa.field("execution_time$overall_percentage", pa.float64()),
    pa.field("execution_time$initialization", pa.float64()),
    pa.field("execution_time$processing", pa.float64()),
    pa.field("execution_time$synchronization", pa.float64()),
    pa.field("execution_time$local_disk_io", pa.float64()),
    pa.field("execution_time$remote_disk_io", pa.float64()),
    pa.field("execution_time$network_communication", pa.float64()),
    # OPERATOR_STATISTICS shreds
    pa.field("operator_stats$input_rows", pa.int64()),
    pa.field("operator_stats$output_rows", pa.int64()),
    pa.field("operator_stats$network_bytes", pa.int64()),
    pa.field("operator_stats$bytes_spilled_local_storage", pa.int64()),
    pa.field("operator_stats$bytes_spilled_remote_storage", pa.int64()),
    pa.field("operator_stats$bytes_scanned", pa.int64()),
    pa.field("operator_stats$bytes_written", pa.int64()),
    pa.field("operator_stats$bytes_written_to_result", pa.int64()),
    pa.field("operator_stats$partitions_scanned", pa.int64()),
    pa.field("operator_stats$partitions_total", pa.int64()),
    # OPERATOR_ATTRIBUTES shreds
    pa.field("operator_attrs$grouping_keys", pa.list_(pa.string())),
    pa.field("operator_attrs$join_type", pa.string()),
    pa.field("operator_attrs$equality_join_condition", pa.string()),
    pa.field("operator_attrs$additional_join_condition", pa.string()),
    pa.field("operator_attrs$table_name", pa.string()),
    pa.field("operator_attrs$filter_condition", pa.string()),
    pa.field("operator_attrs$join_id", pa.int32()),
    pa.field("operator_attrs$columns", pa.list_(pa.string())),
    pa.field("operator_attrs$expressions", pa.list_(pa.string())),
    pa.field("operator_attrs$functions", pa.list_(pa.string())),
    # VARIANT remainders after OBJECT_DELETE — serialized as JSON strings till we can use VARIANT.
    pa.field("operator_statistics", pa.string()),
    pa.field("operator_attributes", pa.string()),
])


def _coerce_column(values: list[Any], arrow_type: pa.DataType) -> list[Any]:
    """Normalise a column's values to match arrow_type.

    The Snowflake connector can return numeric columns as Python strings
    (e.g. '6' instead of 6) depending on connector version and session
    settings. PyArrow refuses to cast str → int32/int64/float64, so we
    coerce here, driven entirely by the schema so new fields are covered
    automatically.
    """
    if pa.types.is_integer(arrow_type):
        return [None if v is None else int(v) for v in values]
    if pa.types.is_floating(arrow_type):
        return [None if v is None else float(v) for v in values]
    if pa.types.is_list(arrow_type) and pa.types.is_integer(arrow_type.value_type):
        # list_(int32): each element may arrive as a string
        result: list[Any] = []
        for v in values:
            if v is None:
                result.append(None)
            elif isinstance(v, list):
                result.append([None if x is None else int(x) for x in v])
            else:
                result.append(None)
        return result
    return values


def rows_to_table(rows: list[dict[str, Any]]) -> pa.Table:
    """Convert a list of dict rows into a pyarrow Table matching ARROW_SCHEMA."""
    if not rows:
        return ARROW_SCHEMA.empty_table()

    _JSON_FIELDS = {"operator_statistics", "operator_attributes"}
    columns: dict[str, list[Any]] = {f.name: [] for f in ARROW_SCHEMA}
    for row in rows:
        for name in columns:
            val = row.get(name)
            if name in _JSON_FIELDS and val is not None and not isinstance(val, str):
                val = json.dumps(val, default=str)
            columns[name].append(val)

    # Coerce numeric columns that the connector returned as strings.
    columns = {f.name: _coerce_column(columns[f.name], f.type) for f in ARROW_SCHEMA}

    return pa.Table.from_pydict(columns, schema=ARROW_SCHEMA)


def write_shard(rows: list[dict[str, Any]], out_path: Path) -> int:
    """Write rows to a Parquet file. Returns row count written."""
    table = rows_to_table(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # SNAPPY is Snowflake's default expectation and has the best
    # decode speed/size tradeoff for COPY INTO.
    pq.write_table(table, out_path, compression="snappy")
    return table.num_rows
