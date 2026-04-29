"""CLI entrypoint: `python -m query_operator_stats`."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .pipeline import run


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="query-operator-stats",
        description="Parallel extractor for Snowflake GET_QUERY_OPERATOR_STATS.",
    )
    p.add_argument("--config", help="Path to config.toml (default: auto-discover)")
    p.add_argument("--lookback-hours", type=int, help="Override pipeline.lookback_hours")
    p.add_argument("--min-execute-minutes", type=int, help="Override pipeline.min_execute_minutes")
    p.add_argument("--workers", type=int, help="Override pipeline.workers")
    p.add_argument("--max-queries", type=int, help="Override pipeline.max_queries_per_run")
    p.add_argument("--log-level", help="DEBUG|INFO|WARNING|ERROR")
    p.add_argument("--dry-run", action="store_true",
                   help="List candidates but skip extract/load")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = load_config(args.config)

    # Apply CLI overrides (highest precedence).
    if args.lookback_hours is not None:
        cfg.pipeline.lookback_hours = args.lookback_hours
    if args.min_execute_minutes is not None:
        cfg.pipeline.min_execute_minutes = args.min_execute_minutes
    if args.workers is not None:
        cfg.pipeline.workers = args.workers
    if args.max_queries is not None:
        cfg.pipeline.max_queries_per_run = args.max_queries
    if args.log_level is not None:
        cfg.pipeline.log_level = args.log_level

    logging.basicConfig(
        level=getattr(logging, cfg.pipeline.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.dry_run:
        from .snowflake_io import connect, list_candidate_queries
        conn = connect(cfg)
        try:
            rows = list_candidate_queries(conn, cfg)
        finally:
            conn.close()
        print(f"dry-run: {len(rows)} candidate queries would be extracted")
        for r in rows[:10]:
            print(f"  {r['QUERY_ID']}  {r['START_TIME']}  {r['EXECUTION_TIME']}ms")
        if len(rows) > 10:
            print(f"  ... and {len(rows) - 10} more")
        return 0

    report = run(cfg)
    logging.getLogger(__name__).info(
        "pipeline done in %.1fs: candidates=%d ok=%d failed=%d "
        "rows_local=%d rows_loaded=%d shards=%d",
        report.duration_seconds,
        report.candidates_found,
        report.queries_succeeded,
        report.queries_failed,
        report.rows_written_local,
        report.rows_loaded_remote,
        report.shards_written,
    )
    return 0 if report.queries_failed == 0 else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
