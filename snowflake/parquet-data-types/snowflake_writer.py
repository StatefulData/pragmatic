"""
sf_parquet_harness_v3/src/snowflake_writer.py

Production integration: connects to Snowflake, runs the SQL, and writes
Parquet using both Approach A (Arrow batches) and Approach B (dict path).

Usage:
    python -m src.snowflake_writer \
        --account  myaccount.us-east-1 \
        --user     my_user \
        --warehouse MY_WH \
        --database  MY_DB \
        --schema    MY_SCHEMA \
        --n-rows    2000 \
        --out-dir   ./output

Requires: snowflake-connector-python[pandas,pyarrow]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from schema import SCHEMA
from writer_approach_a import _cast_raw_to_target
from writer_approach_b import _build_table


# ---------------------------------------------------------------------------
# Snowflake connection (lazy import so module loads without connector installed)
# ---------------------------------------------------------------------------

def _get_connector():
    try:
        import snowflake.connector
        return snowflake.connector
    except ImportError:
        raise ImportError(
            "snowflake-connector-python is required for production use.\n"
            "Install: pip install snowflake-connector-python[pandas,pyarrow]"
        )


def fetch_approach_a(cursor, sql: str) -> tuple[pa.Table, float]:
    """
    Approach A: use cursor.fetch_arrow_batches() to get Arrow RecordBatches.
    Returns (table, elapsed_seconds).

    NOTE:
      - Requires arrow_number_to_decimal=True in connection params for DECIMAL.
      - VARIANT/OBJECT/ARRAY columns arrive as pa.string() (JSON).
      - TIMESTAMP arrives as pa.int64() (epoch at connector precision).
    """
    t0 = time.perf_counter()
    cursor.execute(sql)

    batches = list(cursor.fetch_arrow_batches())
    if not batches:
        raw_table = pa.table({})
    elif isinstance(batches[0], pa.Table):
        raw_table = pa.concat_tables(batches)
    else:
        raw_table = pa.Table.from_batches(batches)

    # Cast raw Snowflake Arrow schema to target schema
    target_table = _cast_raw_to_target_from_sf(raw_table)
    elapsed = time.perf_counter() - t0
    return target_table, elapsed


def fetch_approach_b(cursor, sql: str) -> tuple[pa.Table, float]:
    """
    Approach B: cursor.fetchall(as_dict=True) then per-column conversion.
    Returns (table, elapsed_seconds).
    """
    t0 = time.perf_counter()
    cursor.execute(sql)
    rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else []
    # DictCursor returns uppercase keys; lowercase to match schema
    plain_rows = [{k.lower(): v for k, v in dict(r).items()} for r in rows]
    table = _build_table(plain_rows)
    elapsed = time.perf_counter() - t0
    return table, elapsed


def _cast_raw_to_target_from_sf(raw: pa.Table) -> pa.Table:
    """
    Adapter: Snowflake Arrow output has different column names/types than simulation.
    This normalizes the raw SF Arrow table before passing to _cast_raw_to_target.

    Key differences from simulation:
    - SF returns column names in UPPERCASE
    - DECIMAL may come as string or int depending on arrow_number_to_decimal setting
    - DATE may come as pa.date32() already
    - TIMESTAMP_NTZ comes as pa.int64() with precision encoded in metadata

    This function is intentionally incomplete — you must adapt it for your
    specific Snowflake setup and connector version.
    """
    # Lowercase all column names to match schema
    new_cols = {}
    for i, col_name in enumerate(raw.schema.names):
        new_cols[col_name.lower()] = raw.column(i)

    raw_lower = pa.table(new_cols)
    return _cast_raw_to_target(raw_lower)


def run(args):
    connector = _get_connector()

    # Connection parameters
    conn_params = {
        "account":   args.account,
        "user":      args.user,
        "warehouse": args.warehouse,
        "database":  args.database,
        "schema":    args.schema,
        # Required for proper DECIMAL handling in Arrow mode
        "arrow_number_to_decimal": True,
    }

    # Password or private key
    if args.password:
        conn_params["password"] = args.password
    elif args.private_key_path:
        from cryptography.hazmat.primitives import serialization
        with open(args.private_key_path, "rb") as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=args.passphrase.encode() if args.passphrase else None
            )
        conn_params["private_key"] = private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()
        )
    else:
        raise ValueError("Provide --password OR --private-key-path --passphrase")

    sql_path = Path(__file__).parent / "generate_data.sql"
    sql = sql_path.read_text().replace("ROWCOUNT => 2000", f"ROWCOUNT => {args.n_rows}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to Snowflake ({args.account})...")
    with connector.connect(**conn_params) as conn:
        print(f"Running SQL for {args.n_rows} rows...")

        # --- Approach A ---
        with conn.cursor() as cur:
            table_a, t_a = fetch_approach_a(cur, sql)
            path_a = out_dir / "approach_a.parquet"
            pq.write_table(table_a, str(path_a), compression="snappy",
                           write_statistics=True)
            print(f"Approach A: {t_a:.2f}s -> {path_a} ({path_a.stat().st_size:,} bytes)")

        # --- Approach B ---
        with conn.cursor(connector.DictCursor) as cur:
            table_b, t_b = fetch_approach_b(cur, sql)
            path_b = out_dir / "approach_b.parquet"
            pq.write_table(table_b, str(path_b), compression="snappy",
                           write_statistics=True)
            print(f"Approach B: {t_b:.2f}s -> {path_b} ({path_b.stat().st_size:,} bytes)")

    print(f"\nApproach A vs B speedup: {t_b/t_a:.1f}x")

    # --- Cross-validate ---
    print("\nCross-validating Approach A vs B schemas...")
    for field in SCHEMA:
        ta = table_a.schema.field(field.name).type
        tb = table_b.schema.field(field.name).type
        status = "✓" if ta == tb else "✗"
        if ta != tb:
            print(f"  {status} {field.name}: A={ta} B={tb}")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Snowflake -> Parquet harness v3")
    parser.add_argument("--account",          required=True, help="Org_Name-Account_Name")
    parser.add_argument("--user",             required=True)
    parser.add_argument("--warehouse",        default=None)
    parser.add_argument("--database",         default=None)
    parser.add_argument("--schema",           default=None)
    parser.add_argument("--password",         default=None, help="Use either --password OR --private-key-path + --passphrase")
    parser.add_argument("--private-key-path", default=None)
    parser.add_argument("--passphrase",       default=None)
    parser.add_argument("--n-rows",           type=int, default=8192)
    parser.add_argument("--out-dir",          default="./output")
    run(parser.parse_args())
