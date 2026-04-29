# query_operator_stats

High-throughput extractor for Snowflake's `GET_QUERY_OPERATOR_STATS()` table function.

## What it does

The original stored procedure in [`sql/PUBLIC_GET_EXPENSIVE_QUERY_OPERATOR_STATS.sql`](sql/PUBLIC_GET_EXPENSIVE_QUERY_OPERATOR_STATS.sql) walks expensive queries from `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` in a cursor loop and issues one `INSERT INTO ... SELECT FROM TABLE(GET_QUERY_OPERATOR_STATS(...))` per query. At scale (thousands of queries per lookback window) this suffers from two problems:

1. **Sequential execution**: one roundtrip per `QUERY_ID`, no parallelism.
2. **Micro-partition fragmentation**: thousands of tiny INSERT statements create thousands of under-sized micro-partitions in the target table, degrading subsequent reads until background consolidation catches up.

This module replaces that pattern with an async fan-out + bulk-load pipeline:

```
 ┌─ step 1 ──────────────┐   ┌─ step 2 ────────────────────┐   ┌─ step 3 ────────┐   ┌─ step 4 ─────┐
 │ list candidate        │   │ N concurrent queries via    │   │ PUT parquet →   │   │ cleanup      │
 │ QUERY_IDs not yet in  │ ─▶│ execute_async on ONE        │ ─▶│ stage, then     │ ─▶│ local files  │
 │ the history table     │   │ connection; rows flushed    │   │ COPY INTO       │   │              │
 │                       │   │ into N parquet shards       │   │ target table    │   │              │
 └───────────────────────┘   └─────────────────────────────┘   └─────────────────┘   └──────────────┘
```

## Why `execute_async` + `asyncio` (and not a thread pool)

The Snowflake Python connector exposes `Cursor.execute_async()`, which submits a query to the server and returns immediately with a query ID (`cur.sfqid`). We then poll `conn.get_query_status()` and retrieve results with `cur.get_results_from_sfqid()` once done.

Because `execute_async` is truly non-blocking on the server side, **one Snowflake connection can host many queries in flight simultaneously** — there is no session-level serialization for async queries the way there is for `execute()`. `asyncio` drives concurrency with a `Semaphore(N)` cap; polls yield the event loop via `await asyncio.sleep(...)`.

Advantages over a thread pool + many connections:

- **One connection, one session cost**: no per-worker login handshake, no OCSP cache churn, no N simultaneous warehouse-context sessions.
- **Lower memory footprint**: the connector allocates significant buffers per connection.
- **Native `asyncio` composition**: makes the polling loop, timeouts, and shutdown trivial to reason about.
- **Matches the Snowflake-recommended pattern** documented at <https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-api>.

## Layout

```
query_operator_stats/
├── README.md
├── CLAUDE.md                     # context for future Claude Code sessions
├── pyproject.toml
├── config/
│   └── config.example.toml       # copy to config.toml and edit
├── sql/
│   ├── PUBLIC_GET_EXPENSIVE_QUERY_OPERATOR_STATS.sql   # original procedure (kept for reference)
│   ├── 001_create_target_table.sql                     # DDL for DATA.QUERY_OPERATOR_STATS_HISTORY
│   └── 002_create_stage.sql                            # DDL for the internal stage used by COPY
├── src/query_operator_stats/
│   ├── __init__.py
│   ├── __main__.py               # CLI: python -m query_operator_stats
│   ├── config.py                 # TOML + env var loader
│   ├── snowflake_io.py           # connection, candidate listing, stage PUT, COPY INTO
│   ├── extractor.py              # asyncio + execute_async fan-out
│   ├── parquet_writer.py         # schema + pyarrow shard writer
│   └── pipeline.py               # orchestrates all 4 steps
└── tests/
    ├── test_config.py
    └── test_parquet_writer.py
```

## Quick start

```bash
# 1. install
pip install -e .

# 2. configure
cp config/config.example.toml config/config.toml
# edit config.toml, or export SNOWFLAKE_* environment variables

# 3. create target objects (one-time)
snowsql -f sql/001_create_target_table.sql
snowsql -f sql/002_create_stage.sql

# 4. run (defaults: 4h lookback, 10min min_execute, 8 concurrent queries)
python -m query_operator_stats --lookback-hours 4 --min-execute-minutes 10 --workers 8
```

## Configuration precedence

1. CLI flags (highest)
2. Environment variables prefixed `SNOWFLAKE_` / `QOS_`
3. `config/config.toml`
4. Built-in defaults (lowest)

## Operational notes

- **Authentication**: Key-pair auth is the default and recommended path. Password auth is supported for dev/test only.
- **Warehouse sizing**: `GET_QUERY_OPERATOR_STATS` is a metadata-bound call — `XS` is plenty. The COPY INTO also runs on the same warehouse and is fast for shard counts ≤ 32.
- **`ABORT_DETACHED_QUERY` must remain `FALSE`** (default) for `execute_async` to work reliably. Don't enable it on the service-user session.
- **Idempotency**: The candidate selection antijoins against the target table on `QUERY_ID`, so re-running the same window is safe. Failed queries are naturally retried on the next run.
- **Stage hygiene**: `COPY INTO` runs with `PURGE = TRUE` to auto-delete files after a successful load. A defensive `REMOVE @stage` runs before each PUT to clean up any orphans from a prior failed run.
