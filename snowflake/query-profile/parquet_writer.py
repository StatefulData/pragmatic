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
    pa.field("parent_operator_id", pa.int32()),
    pa.field("operator_type", pa.string()),
    pa.field("execution_time$overall_percentage", pa.float64()),
    pa.field("execution_time$local_disk_io", pa.float64()),
    pa.field("execution_time$network_communication", pa.float64()),
    pa.field("operator_stats$input_rows", pa.int64()),
    pa.field("operator_stats$output_rows", pa.int64()),
    pa.field("operator_stats$network_bytes", pa.int64()),
    pa.field("operator_stats$bytes_spilled_local_storage", pa.int64()),
    pa.field("operator_stats$bytes_spilled_remote_storage", pa.int64()),
    # VARIANT on the Snowflake side — serialize as JSON string here.
    pa.field("operator_attributes", pa.string()),
])


def rows_to_table(rows: list[dict[str, Any]]) -> pa.Table:
    """Convert a list of dict rows into a pyarrow Table matching ARROW_SCHEMA."""
    if not rows:
        return ARROW_SCHEMA.empty_table()

    columns: dict[str, list[Any]] = {f.name: [] for f in ARROW_SCHEMA}
    for row in rows:
        for name in columns:
            val = row.get(name)
            if name == "operator_attributes" and val is not None and not isinstance(val, str):
                val = json.dumps(val, default=str)
            columns[name].append(val)

    return pa.Table.from_pydict(columns, schema=ARROW_SCHEMA)


def write_shard(rows: list[dict[str, Any]], out_path: Path) -> int:
    """Write rows to a Parquet file. Returns row count written."""
    table = rows_to_table(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # SNAPPY is Snowflake's default expectation and has the best
    # decode speed/size tradeoff for COPY INTO.
    pq.write_table(table, out_path, compression="snappy")
    return table.num_rows
