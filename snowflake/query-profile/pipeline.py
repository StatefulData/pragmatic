"""4-step pipeline orchestrator.

    (1) list candidate QUERY_IDs
    (2) asyncio + execute_async fan-out → local Parquet shards
    (3) PUT shards to stage → COPY INTO target table
    (4) cleanup local scratch files

Step 2 uses a single Snowflake connection with many concurrent async queries
(see extractor.py). Step 3 uses a separate, short-lived connection because
PUT/COPY are synchronous operations that don't benefit from the async path.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .extractor import extract_all
from .snowflake_io import (
    connect,
    copy_into_target,
    list_candidate_queries,
    put_shards_to_stage,
    remove_stage_files,
)

log = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    candidates_found: int
    queries_succeeded: int
    queries_failed: int
    rows_written_local: int
    rows_loaded_remote: int
    shards_written: int
    duration_seconds: float


async def run_pipeline(cfg: Config) -> PipelineReport:
    t0 = time.monotonic()
    scratch_dir = Path(cfg.pipeline.scratch_dir)

    # Step 1: candidates — short-lived connection, single sync query.
    list_conn = connect(cfg)
    try:
        candidates = list_candidate_queries(list_conn, cfg)
    finally:
        list_conn.close()

    if not candidates:
        log.info("no candidate queries found — nothing to do")
        return PipelineReport(0, 0, 0, 0, 0, 0, time.monotonic() - t0)

    # Step 2: async fan-out → local Parquet shards.
    extract = await extract_all(cfg, candidates, scratch_dir)

    if not extract.shard_paths:
        log.warning("no shards produced — skipping load")
        _cleanup(scratch_dir)
        return PipelineReport(
            candidates_found=len(candidates),
            queries_succeeded=extract.queries_succeeded,
            queries_failed=len(extract.queries_failed),
            rows_written_local=0,
            rows_loaded_remote=0,
            shards_written=0,
            duration_seconds=time.monotonic() - t0,
        )

    # Step 3: PUT + COPY INTO on a fresh connection.
    rows_loaded = 0
    load_conn = connect(cfg)
    try:
        remove_stage_files(load_conn, cfg.target.stage)  # defensive cleanup
        put_shards_to_stage(load_conn, cfg.target.stage, extract.shard_paths)
        rows_loaded = copy_into_target(load_conn, cfg)
    finally:
        load_conn.close()

    # Step 4: cleanup scratch.
    _cleanup(scratch_dir)

    return PipelineReport(
        candidates_found=len(candidates),
        queries_succeeded=extract.queries_succeeded,
        queries_failed=len(extract.queries_failed),
        rows_written_local=extract.rows_written,
        rows_loaded_remote=rows_loaded,
        shards_written=len(extract.shard_paths),
        duration_seconds=time.monotonic() - t0,
    )


def _cleanup(scratch_dir: Path) -> None:
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir, ignore_errors=True)
        log.debug("scratch dir %s removed", scratch_dir)


def run(cfg: Config) -> PipelineReport:
    """Sync wrapper around run_pipeline — for CLI and cron use."""
    return asyncio.run(run_pipeline(cfg))
