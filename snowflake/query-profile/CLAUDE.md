# CLAUDE.md — notes for Claude Code working in this module

Context that isn't obvious from the code.

## Purpose

Replace the cursor-loop stored procedure in `sql/GET_EXPENSIVE_QUERY_OPERATOR_STATS.sql` with a parallel Python extractor that:
1. Lists QUERY_IDs from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` not yet in the log table.
2. Submits N concurrent `GET_QUERY_OPERATOR_STATS()` calls via `Cursor.execute_async()` on a single Snowflake connection, driven by `asyncio`.
3. Rows are bucketed into N shard buffers and flushed as one single Parquet fil coordinated via a mutex lock.
4. PUTs the Parquet file to an internal stage and runs a single `COPY INTO`.
5. Cleans up local scratch.
6. Log all the metrics: how many Query_IDs detected by the initial diff, how many Query_IDs still have profiles returned by the function, how many records write to Parquet file, how many bytes before compression, how many bytes after compression in Parquet, how many records merged into Snowflake target table. This is considered as `PipelineReport`.

## Why `execute_async` + `asyncio` and NOT `ThreadPoolExecutor`

Earlier iterations used one connection per worker thread. That works, but is wasteful: each connection is a full authenticated session with its own memory, OCSP cache, and handshake cost. The Snowflake connector's `execute_async` submits a query server-side and returns immediately with `sfqid`; the server does the work, and the client just polls for completion. One connection can host many in-flight async queries concurrently, which is exactly what it needs to be.

**If you're tempted to reintroduce threads**: don't. The only reason to use threads with the Snowflake connector is if you need true parallel `execute()` calls (synchronous), which you don't here. `asyncio` + `execute_async` is a better fit and is the Snowflake-recommended pattern.

## Architectural constraints to preserve

- **One Snowflake connection for Step 2.** Multiple connections defeat the point.
- **`asyncio.Semaphore(workers)` caps concurrency.** This is what maps to the user-facing "workers" knob.
- **All blocking connector calls go through `loop.run_in_executor(None, …)`.** `execute_async` itself, `get_query_status_throw_if_error`, `get_results_from_sfqid`, and `fetchall` are all synchronous HTTP. Running them on the default thread pool executor keeps the event loop responsive so other coroutines can poll/submit during the wait.
- **Shard buffer writes are under `asyncio.Lock()`.** Coroutines in a single event loop are cooperative, but any `await` inside `_extract_one` is a yield point, so `.rows.extend()` + `.queries_succeeded += 1` must be guarded to avoid interleaving.
- **Round-robin into N shards, NOT one file per query.** `GET_QUERY_OPERATOR_STATS` returns 20-200 rows per query. One file per query would re-create the small-file problem we designed around. Each Parquet row group can pack 1000~5000 rows, once a row group has accumulated enough rows within a given worker shard, that row group will be flush to the Parquet file. Multiple workers can obtain the lock and flush into a single Parquet file.
- **Arrow schema in `parquet_writer.py` must match `sql/001_create_target_table.sql` exactly.** If you change one, change both — and update the `$1:<field>` projection inside `_COPY_SQL` in `snowflake_io.py`.
- Due to some data type conversion caveat between Snowflake and Python, need to log the schema of the recordset of Snowflake cursor when the very first chunk is returned
- https://docs.snowflake.com/en/sql-reference/functions/get_query_operator_stats has all info about `Key`, `Data Type` and `Description` for the top-level JSON fields such as OPERATOR_ATTRIBUTES and OPERATOR_STATISTICS. We will add the Keys into the Parquet field description for the same nested Key which we flatten/shred into <parent_key>$<nested_key>.
- Everything inside `EXECUTION_TIME_BREAKDOWN` needs to be shredded. `OPERATOR_STATISTICS` and `OPERATOR_ATTRIBUTES` will be partially shredded.
- For those shredded/flatterned keys, use OBJECT_DELETE() to remove them from the VARIANT output fields
- **`OPERATOR_ATTRIBUTES` is VARIANT in Snowflake but serialized as a JSON string in Parquet.** The COPY uses `TRY_PARSE_JSON` to convert back. Don't try to store it as a Parquet struct — Snowflake's Parquet VARIANT ingest is finicky and the JSON-string path is the reliable one.
- **PUT uses `AUTO_COMPRESS=FALSE`** because Parquet files are already SNAPPY-compressed. Auto-compress wraps them in gzip and breaks Parquet parsing on COPY.
- **PUT / COPY are synchronous.** `execute_async` has a known bug with `PUT` commands (snowflake-connector-python issue #1227). Keep them on the sync `execute()` path — they run in Step 3 after the async fan-out has fully drained.

## Session settings that matter

- **`ABORT_DETACHED_QUERY` must remain `FALSE`** (default). If someone enables it on the service role, async queries get cancelled when the session ends. The module does not alter this parameter; if you want to set it, add `ALTER SESSION SET ABORT_DETACHED_QUERY = FALSE;` as an explicit safeguard, but don't flip it to TRUE.
- **Autocommit is fine at default (True).** The pipeline issues one statement per step; no multi-statement transactions to coordinate.

## Known rough edges / good next tasks

- No retry on individual `GET_QUERY_OPERATOR_STATS` failures. A transient failure drops that query into the failed list; it'll be re-picked on the next run via the antijoin. A small retry loop inside `_extract_one` (2-3 attempts, jittered backoff) would be cheap and worthwhile.
- Polling uses fixed exponential backoff. For a bimodal workload (many fast queries + a few slow ones), an adaptive heuristic (track per-query observed runtime) would reduce poll count, but isn't worth the complexity until profiling shows it matters.
- No metrics emission — `PipelineReport` is returned but not exported. OTel or StatsD is a natural add.
- The stored procedure in `sql/GET_EXPENSIVE_QUERY_OPERATOR_STATS.sql` is kept for reference and for users who prefer a pure-SQL deployment. It is **not** invoked by this module.
- `USE_LOGICAL_TYPE = TRUE` must be specified for `COPY INTO` or `CREATE FILE FORMAT`, otherwise Snowflake may treate TIMESTAMP fields in Parquet files as Epoch Second blindly.

## Don'ts

- Don't need to create N Parquet files to match N workers because Parquet compression ratio is very high. Even with 1M rows, the file size is still small. We don't need to worry about "a huge Parquet file" issue. We may need to backup such Parquet files on S3, so fewer number of files is better.
- Don't switch `COPY INTO` to `ON_ERROR = 'CONTINUE'` silently. `ABORT_STATEMENT` is intentional: a failed COPY leaves stage files for forensics, and the antijoin ensures clean re-attempt on the next run.
- Don't add Time Travel retention to the target table. Transient + 1-day default Time Travel + zero Fail-safe is the cost-correct posture for derivable log data.
- Don't run `execute_async` on a `PUT` command. It raises `KeyError: 'command'` and silently falls back to sync `execute()` — surprising and brittle.

## Test strategy

Tests in `tests/` cover pure-Python logic only (config loading, Parquet schema round-trip). Live-Snowflake integration is covered by `--dry-run` during manual verification. If you add integration tests, gate them with `QOS_RUN_INTEGRATION_TESTS=1` so CI doesn't hit a real account.

## Running locally

```bash
pip install -e .[dev]
cp config/config.example.toml config/config.toml               # then edit
python __main__.py --config /path/to/config.toml --dry-run     # lists candidates only
python __main__.py --workers 8                                 # full run
pytest -v
```

Source files live in the project root (not inside `query_operator_stats/`), so they must be run as scripts (`python __main__.py`) rather than as a package (`python -m`). All inter-module imports are absolute so the current working directory must be the project root.
