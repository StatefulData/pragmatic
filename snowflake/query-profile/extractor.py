"""Async fan-out over GET_QUERY_OPERATOR_STATS using `execute_async`.

Design
------
The Snowflake Python connector supports truly asynchronous query submission
via `Cursor.execute_async()` — it returns immediately after the server accepts
the query, exposing the query ID as `cur.sfqid`. We then poll
`Connection.get_query_status()` until `is_still_running()` returns False, and
retrieve the result set with `get_results_from_sfqid()`.

This lets us hold many queries in flight on a *single* Snowflake connection
concurrently, driven by `asyncio`. There is no Python GIL contention because
the polling loop is a proper `await asyncio.sleep(...)` — the event loop is
free to schedule every other coroutine during the wait.

Concurrency control
-------------------
An `asyncio.Semaphore(workers)` caps the number of queries in flight at any
instant. This maps directly to the "8 parallel" requirement — 8 queries run
at the server concurrently, each with its own independent warehouse slot.

Sharding strategy
-----------------
A naive implementation would write one Parquet file per query, recreating
the small-file problem we already designed around. Instead, we assign each
query a "shard slot" by round-robin index (modulo workers), and all queries
for a given slot append to the same in-memory row buffer. At the end, each
slot's buffer is flushed to exactly one Parquet file — yielding `workers`
shards regardless of how many queries were processed.

Caveats
-------
* `execute_async` explicitly does NOT work for `PUT` / `GET` commands. Those
  stay synchronous in `snowflake_io.py`. The async path is only used for the
  `GET_QUERY_OPERATOR_STATS` extraction step.
* `ABORT_DETACHED_QUERY` must remain `FALSE` (default) or async queries can
  be cancelled when the session closes. We don't change it.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import snowflake.connector
from snowflake.connector.constants import FIELD_ID_TO_NAME
from snowflake.connector.errors import ProgrammingError

from config import Config
from parquet_writer import write_shard
from snowflake_io import connect

log = logging.getLogger(__name__)

# How long to sleep between status polls. Snowflake's internal minimum query
# latency is ~0.5s; polling faster than that just burns cloud-services calls.
_INITIAL_POLL_INTERVAL = 0.5
_MAX_POLL_INTERVAL = 4.0
_POLL_BACKOFF_FACTOR = 1.5


def _as_dict(val: Any) -> dict[str, Any]:
    """Coerce a VARIANT/OBJECT cursor value to dict.

    The Snowflake connector can return OBJECT columns as either a Python dict
    or a raw JSON string depending on session/driver settings. Handles both.
    """
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass
    return {}


def _coerce_int_list(val: Any) -> list[int] | None:
    """Coerce an ARRAY cursor value to list[int], tolerating VARCHAR elements.

    GET_QUERY_OPERATOR_STATS returns PARENT_OPERATORS as ARRAY, but the
    connector may surface elements as strings. Parquet schema expects int32.
    Returns None (not []) so nullable Parquet list columns stay NULL when
    the operator has no parents.
    """
    if val is None:
        return None
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (ValueError, TypeError):
            return None
    if not isinstance(val, list):
        return None
    coerced: list[int] = []
    for x in val:
        if x is None:
            continue
        try:
            coerced.append(int(x))
        except (ValueError, TypeError):
            pass
    return coerced or None


@dataclass
class CandidateQuery:
    query_id: str
    query_tag: str | None
    start_time: datetime
    end_time: datetime
    execution_time: int


@dataclass
class ShardBuffer:
    """Accumulates rows destined for a single output Parquet file."""
    slot: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    queries_succeeded: int = 0
    queries_failed: list[str] = field(default_factory=list)


@dataclass
class ExtractResult:
    shard_paths: list[Path]
    rows_written: int
    queries_succeeded: int
    queries_failed: list[str]


def _candidates_from_rows(rows: list[dict[str, Any]]) -> list[CandidateQuery]:
    return [
        CandidateQuery(
            query_id=r["QUERY_ID"],
            query_tag=r.get("QUERY_TAG"),
            start_time=r["START_TIME"],
            end_time=r["END_TIME"],
            execution_time=int(r["EXECUTION_TIME"]),
        )
        for r in rows
    ]


_OP_STATS_SHREDDED = frozenset({
    "input_rows", "output_rows", "network", "io", "spilling", "pruning",
})
_OP_ATTRS_SHREDDED = frozenset({
    "grouping_keys", "join_type", "equality_join_condition", "additional_join_condition",
    "table_name", "filter_condition", "join_id", "columns", "expressions", "functions",
})


def _shape_rows(raw_rows: list[tuple], cq: CandidateQuery) -> list[dict[str, Any]]:
    """Flatten GET_QUERY_OPERATOR_STATS output into the target table's schema."""
    out: list[dict[str, Any]] = []
    for row in raw_rows:
        step_id, operator_id, parent_operators, operator_type, exec_bd, op_stats, op_attrs = row
        exec_bd  = _as_dict(exec_bd)
        op_stats = _as_dict(op_stats)
        op_attrs = _as_dict(op_attrs)
        parent_operators = _coerce_int_list(parent_operators)

        network  = op_stats.get("network")  or {}
        io       = op_stats.get("io")       or {}
        spilling = op_stats.get("spilling") or {}
        pruning  = op_stats.get("pruning")  or {}

        # coalesce(table_name, table_names[0]) — mirrors the SQL COALESCE
        table_name = op_attrs.get("table_name")
        if table_name is None:
            table_names = op_attrs.get("table_names")
            if isinstance(table_names, list) and table_names:
                table_name = str(table_names[0])

        # OBJECT_DELETE: strip shredded keys from both VARIANT remainders.
        op_stats_remainder = {k: v for k, v in op_stats.items() if k not in _OP_STATS_SHREDDED} or None
        op_attrs_remainder = {k: v for k, v in op_attrs.items() if k not in _OP_ATTRS_SHREDDED} or None

        out.append({
            "query_id": cq.query_id,
            "query_tag": cq.query_tag,
            "start_time": cq.start_time,
            "end_time": cq.end_time,
            "execution_time": cq.execution_time,
            "step_id": step_id,
            "operator_id": operator_id,
            "parent_operators": parent_operators,
            "operator_type": operator_type,
            "execution_time$overall_percentage":    exec_bd.get("overall_percentage"),
            "execution_time$initialization":        exec_bd.get("initialization"),
            "execution_time$processing":            exec_bd.get("processing"),
            "execution_time$synchronization":       exec_bd.get("synchronization"),
            "execution_time$local_disk_io":         exec_bd.get("local_disk_io"),
            "execution_time$remote_disk_io":        exec_bd.get("remote_disk_io"),
            "execution_time$network_communication": exec_bd.get("network_communication"),
            "operator_stats$input_rows":                    op_stats.get("input_rows"),
            "operator_stats$output_rows":                   op_stats.get("output_rows"),
            "operator_stats$network_bytes":                 network.get("network_bytes"),
            "operator_stats$bytes_spilled_local_storage":   spilling.get("bytes_spilled_local_storage"),
            "operator_stats$bytes_spilled_remote_storage":  spilling.get("bytes_spilled_remote_storage"),
            "operator_stats$bytes_scanned":                 io.get("bytes_scanned"),
            "operator_stats$bytes_written":                 io.get("bytes_written"),
            "operator_stats$bytes_written_to_result":       io.get("bytes_written_to_result"),
            "operator_stats$partitions_scanned":            pruning.get("partitions_scanned"),
            "operator_stats$partitions_total":              pruning.get("partitions_total"),
            "operator_attrs$grouping_keys":             op_attrs.get("grouping_keys"),
            "operator_attrs$join_type":                 op_attrs.get("join_type"),
            "operator_attrs$equality_join_condition":   op_attrs.get("equality_join_condition"),
            "operator_attrs$additional_join_condition": op_attrs.get("additional_join_condition"),
            "operator_attrs$table_name":                table_name,
            "operator_attrs$filter_condition":          op_attrs.get("filter_condition"),
            "operator_attrs$join_id":                   op_attrs.get("join_id"),
            "operator_attrs$columns":                   op_attrs.get("columns"),
            "operator_attrs$expressions":               op_attrs.get("expressions"),
            "operator_attrs$functions":                 op_attrs.get("functions"),
            "operator_statistics": op_stats_remainder,
            "operator_attributes": op_attrs_remainder,
        })
    return out


async def _await_query(
    conn: snowflake.connector.SnowflakeConnection,
    query_id: str,
) -> None:
    """Poll an async query until it's no longer running.

    Uses exponential backoff capped at _MAX_POLL_INTERVAL. Polls are run
    through the event loop's default executor because
    `get_query_status_throw_if_error` is a blocking HTTP call.
    """
    interval = _INITIAL_POLL_INTERVAL
    loop = asyncio.get_running_loop()
    while True:
        status = await loop.run_in_executor(
            None, conn.get_query_status_throw_if_error, query_id
        )
        if not conn.is_still_running(status):
            return
        await asyncio.sleep(interval)
        interval = min(interval * _POLL_BACKOFF_FACTOR, _MAX_POLL_INTERVAL)


async def _extract_one(
    conn: snowflake.connector.SnowflakeConnection,
    cq: CandidateQuery,
    sem: asyncio.Semaphore,
    shard: ShardBuffer,
    buffer_lock: asyncio.Lock,
    schema_logged: asyncio.Event,
) -> None:
    """Submit one GET_QUERY_OPERATOR_STATS call, wait for it, stash rows in the shard."""
    async with sem:
        loop = asyncio.get_running_loop()
        cur = conn.cursor()
        try:
            # Step 1: submit. execute_async is brief but blocking, so run it
            # in the executor to keep the event loop responsive for other coros.
            def _submit() -> None:
                cur.execute_async(
                    "SELECT STEP_ID, OPERATOR_ID, PARENT_OPERATORS, OPERATOR_TYPE, "
                    "EXECUTION_TIME_BREAKDOWN, OPERATOR_STATISTICS, OPERATOR_ATTRIBUTES "
                    "FROM TABLE(GET_QUERY_OPERATOR_STATS(%s))",
                    (cq.query_id,),
                )

            await loop.run_in_executor(None, _submit)
            qid = cur.sfqid
            if not qid:
                raise RuntimeError(f"no sfqid returned for query_id={cq.query_id}")

            # Step 2: poll for completion.
            await _await_query(conn, qid)

            # Step 3: fetch results.
            await loop.run_in_executor(None, cur.get_results_from_sfqid, qid)
            raw = await loop.run_in_executor(None, cur.fetchall)

            # Log cursor schema once per extract_all invocation so we have a
            # ground-truth record of what the connector actually returned —
            # important because VARIANT/OBJECT type codes differ from plain SQL
            # types and the connector's conversion behaviour is session-dependent.
            if not schema_logged.is_set() and cur.description:
                log.info(
                    "resultset schema (query_id=%s): %s",
                    cq.query_id,
                    [{"name": d[0], "type": FIELD_ID_TO_NAME.get(d[1], str(d[1]))} for d in cur.description],
                )
                schema_logged.set()

            # Step 4: shape and append to the shard buffer under lock.
            shaped = _shape_rows(raw, cq)
            async with buffer_lock:
                shard.rows.extend(shaped)
                shard.queries_succeeded += 1

        except (ProgrammingError, Exception) as exc:  # noqa: BLE001
            log.warning("query_id=%s failed: %s", cq.query_id, exc)
            async with buffer_lock:
                shard.queries_failed.append(cq.query_id)
        finally:
            cur.close()


async def extract_all(
    cfg: Config,
    candidate_rows: list[dict[str, Any]],
    scratch_dir: Path,
) -> ExtractResult:
    """Fan candidates out as concurrent async queries on a single connection.

    One Snowflake connection hosts all in-flight queries. Concurrency is
    capped at `cfg.pipeline.workers` via an `asyncio.Semaphore`. Rows are
    round-robin-distributed across `workers` shard buffers so the final
    output is exactly `workers` Parquet files — not one per query.
    """
    candidates = _candidates_from_rows(candidate_rows)
    if not candidates:
        return ExtractResult([], 0, 0, [])

    scratch_dir.mkdir(parents=True, exist_ok=True)
    n_workers = max(1, cfg.pipeline.workers)

    shards = [ShardBuffer(slot=i) for i in range(n_workers)]
    buffer_lock = asyncio.Lock()
    sem = asyncio.Semaphore(n_workers)
    schema_logged = asyncio.Event()

    log.info(
        "dispatching %d queries; concurrency=%d; single connection",
        len(candidates), n_workers,
    )
    t0 = time.monotonic()

    conn = connect(cfg)
    try:
        tasks = [
            _extract_one(conn, cq, sem, shards[i % n_workers], buffer_lock, schema_logged)
            for i, cq in enumerate(candidates)
        ]
        await asyncio.gather(*tasks)
    finally:
        conn.close()

    # Flush each non-empty shard to a Parquet file.
    shard_paths: list[Path] = []
    total_rows = 0
    total_ok = 0
    total_failed: list[str] = []
    for sh in shards:
        total_ok += sh.queries_succeeded
        total_failed.extend(sh.queries_failed)
        if not sh.rows:
            continue
        path = scratch_dir / f"shard_{sh.slot:02d}_{uuid.uuid4().hex[:8]}.parquet"
        written = write_shard(sh.rows, path)
        shard_paths.append(path)
        total_rows += written
        log.info("shard %d: %d rows, %d queries ok → %s",
                 sh.slot, written, sh.queries_succeeded, path.name)

    log.info(
        "extraction complete in %.1fs: %d rows, %d queries ok, %d failed, %d shards",
        time.monotonic() - t0, total_rows, total_ok, len(total_failed), len(shard_paths),
    )
    return ExtractResult(
        shard_paths=shard_paths,
        rows_written=total_rows,
        queries_succeeded=total_ok,
        queries_failed=total_failed,
    )
