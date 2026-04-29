"""Snowflake I/O: connection, candidate listing, PUT to stage, COPY INTO."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from .config import Config

log = logging.getLogger(__name__)


def _load_private_key(path: str, passphrase: str) -> bytes:
    """Load a PEM private key and return the DER bytes that the connector wants."""
    with open(path, "rb") as fh:
        pem = fh.read()
    pk = serialization.load_pem_private_key(
        pem,
        password=passphrase.encode() if passphrase else None,
        backend=default_backend(),
    )
    return pk.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect(cfg: Config) -> snowflake.connector.SnowflakeConnection:
    """Open a Snowflake connection using key-pair or password auth."""
    kwargs: dict[str, Any] = {
        "account": cfg.snowflake.account,
        "user": cfg.snowflake.user,
        "role": cfg.snowflake.role or None,
        "warehouse": cfg.snowflake.warehouse,
        "database": cfg.snowflake.database,
        "schema": cfg.snowflake.schema,
        "client_session_keep_alive": False,
    }
    if cfg.snowflake.private_key_path:
        kwargs["private_key"] = _load_private_key(
            cfg.snowflake.private_key_path,
            cfg.snowflake.private_key_passphrase,
        )
    else:
        kwargs["password"] = cfg.snowflake.password

    return snowflake.connector.connect(**{k: v for k, v in kwargs.items() if v is not None})


# ---------------------------------------------------------------------------
# Step 1: candidate listing
# ---------------------------------------------------------------------------

_CANDIDATE_SQL = """
SELECT QUERY_ID, QUERY_TAG, START_TIME, END_TIME, EXECUTION_TIME
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE CLUSTER_NUMBER > 0
  AND END_TIME IS NOT NULL
  AND EXECUTION_TIME >= %(min_ms)s
  AND START_TIME >= TIMESTAMPADD(HOUR, -%(lookback_hours)s, DATE_TRUNC(HOUR, CURRENT_TIMESTAMP))
  AND QUERY_ID NOT IN (
      SELECT QUERY_ID
      FROM IDENTIFIER(%(target_table)s)
      WHERE START_TIME >= TIMESTAMPADD(HOUR, -%(lookback_hours)s, DATE_TRUNC(HOUR, CURRENT_TIMESTAMP))
  )
ORDER BY START_TIME
LIMIT %(limit)s
"""


def list_candidate_queries(
    conn: snowflake.connector.SnowflakeConnection,
    cfg: Config,
) -> list[dict[str, Any]]:
    """Return the list of QUERY_IDs that are long-running and not yet extracted."""
    params = {
        "min_ms": cfg.pipeline.min_execute_minutes * 60 * 1000,
        "lookback_hours": cfg.pipeline.lookback_hours,
        "target_table": cfg.target.table,
        "limit": cfg.pipeline.max_queries_per_run,
    }
    log.info(
        "listing candidates: lookback=%sh min_execute=%smin cap=%s",
        cfg.pipeline.lookback_hours,
        cfg.pipeline.min_execute_minutes,
        cfg.pipeline.max_queries_per_run,
    )
    with conn.cursor(snowflake.connector.DictCursor) as cur:
        cur.execute(_CANDIDATE_SQL, params)
        rows = cur.fetchall()
    log.info("found %d candidate queries", len(rows))
    return rows  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Step 3: PUT + COPY INTO
# ---------------------------------------------------------------------------

def put_shards_to_stage(
    conn: snowflake.connector.SnowflakeConnection,
    stage: str,
    shard_paths: list[Path],
) -> None:
    """Upload local Parquet shards to the internal stage.

    PUT compresses by default, but our Parquet files are already SNAPPY-compressed,
    so we disable auto_compress to avoid double-compression and Snowflake choking
    on the .gz wrapper when reading Parquet.
    """
    if not shard_paths:
        return
    with conn.cursor() as cur:
        for p in shard_paths:
            # file:// URI form handles paths with spaces cleanly on all platforms.
            uri = p.resolve().as_uri()
            sql = f"PUT '{uri}' @{stage} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
            log.debug("PUT %s", uri)
            cur.execute(sql)
    log.info("uploaded %d shard(s) to @%s", len(shard_paths), stage)


_COPY_SQL = """
COPY INTO IDENTIFIER(%(target_table)s)
    (QUERY_ID, QUERY_TAG, START_TIME, END_TIME, EXECUTION_TIME,
     STEP_ID, OPERATOR_ID, PARENT_OPERATOR_ID, OPERATOR_TYPE,
     EXECUTION_TIME$OVERALL_PERCENTAGE, EXECUTION_TIME$LOCAL_DISK_IO,
     EXECUTION_TIME$NETWORK_COMMUNICATION,
     OPERATOR_STATS$INPUT_ROWS, OPERATOR_STATS$OUTPUT_ROWS,
     OPERATOR_STATS$NETWORK_BYTES,
     OPERATOR_STATS$BYTES_SPILLED_LOCAL_STORAGE,
     OPERATOR_STATS$BYTES_SPILLED_REMOTE_STORAGE,
     OPERATOR_ATTRIBUTES)
FROM (
    SELECT
        $1:query_id::string,
        $1:query_tag::string,
        $1:start_time::timestamp_ntz,
        $1:end_time::timestamp_ntz,
        $1:execution_time::int,
        $1:step_id::int,
        $1:operator_id::int,
        $1:parent_operator_id::int,
        $1:operator_type::string,
        $1:"execution_time$overall_percentage"::float,
        $1:"execution_time$local_disk_io"::float,
        $1:"execution_time$network_communication"::float,
        $1:"operator_stats$input_rows"::int,
        $1:"operator_stats$output_rows"::int,
        $1:"operator_stats$network_bytes"::int,
        $1:"operator_stats$bytes_spilled_local_storage"::int,
        $1:"operator_stats$bytes_spilled_remote_storage"::int,
        TRY_PARSE_JSON($1:operator_attributes::string)
    FROM @{stage}
)
FILE_FORMAT = (TYPE = PARQUET)
ON_ERROR = 'ABORT_STATEMENT'
PURGE = TRUE
"""


def copy_into_target(
    conn: snowflake.connector.SnowflakeConnection,
    cfg: Config,
) -> int:
    """Execute COPY INTO from stage to target table. Returns rows loaded."""
    # Substitute stage into the SQL template because @stage can't be parameterized.
    sql = _COPY_SQL.format(stage=cfg.target.stage)
    with conn.cursor(snowflake.connector.DictCursor) as cur:
        cur.execute(sql, {"target_table": cfg.target.table})
        result = cur.fetchall()

    rows_loaded = sum(int(r.get("rows_loaded", 0) or 0) for r in result)
    files_loaded = len(result)
    log.info("COPY INTO loaded %d rows from %d file(s)", rows_loaded, files_loaded)

    errors = [r for r in result if (r.get("errors_seen") or 0) > 0]
    if errors:
        log.warning("COPY INTO had errors on %d file(s): %s", len(errors), errors)

    return rows_loaded


def remove_stage_files(
    conn: snowflake.connector.SnowflakeConnection,
    stage: str,
) -> None:
    """Belt-and-suspenders cleanup. COPY INTO ran with PURGE=TRUE already,
    but we call REMOVE to clean up any files from a prior failed run."""
    with conn.cursor() as cur:
        cur.execute(f"REMOVE @{stage}")
    log.debug("stage @%s cleared", stage)
