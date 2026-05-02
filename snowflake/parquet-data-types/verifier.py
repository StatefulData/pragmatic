"""
sf_parquet_harness_v3/src/verifier.py

Data parity verifier: reads back Parquet files and compares against
the original source rows (Python dicts from generator or Snowflake).

Handles type-aware equality for all column types:
  - Decimal (exact)
  - float (within 1e-9)
  - date (via date32 days round-trip)
  - datetime / timestamp (microsecond resolution)
  - bytes (exact)
  - list (element-wise recursive)
  - dict / struct (field-wise recursive)
  - JSON string variants (parse then compare)
  - None / null
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from converters import to_date32, to_ts_us, to_ts_ns, to_ts_ms, to_decimal
from schema import SCHEMA, ALWAYS_NULL_COLS, VARIANT_COLS

_FLOAT_TOL = 1e-9
_EPOCH_DATE = date(1970, 1, 1)
_EPOCH_DT   = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Type-aware equality
# ---------------------------------------------------------------------------

def _eq(a: Any, b: Any, col: str = "", idx: int = -1) -> tuple[bool, str]:
    """Return (equal, reason) for two values."""
    if a is None and b is None:
        return True, ""
    if (a is None) != (b is None):
        return False, f"col={col} row={idx}: one is None ({a!r} vs {b!r})"

    # float tolerance
    if isinstance(a, float) and isinstance(b, float):
        ok = abs(a - b) < _FLOAT_TOL
        return ok, "" if ok else f"col={col} row={idx}: float diff {abs(a-b):.2e}"

    # Decimal exact
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        ok = a == b
        return ok, "" if ok else f"col={col} row={idx}: Decimal {a!r} vs {b!r}"

    # list / array — recursive element-wise
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False, f"col={col} row={idx}: list len {len(a)} vs {len(b)}"
        for i, (ea, eb) in enumerate(zip(a, b)):
            ok, reason = _eq(ea, eb, col=f"{col}[{i}]", idx=idx)
            if not ok:
                return False, reason
        return True, ""

    # dict / struct — field-wise
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False, f"col={col} row={idx}: dict keys {set(a.keys())} vs {set(b.keys())}"
        for k in a:
            ok, reason = _eq(a[k], b[k], col=f"{col}.{k}", idx=idx)
            if not ok:
                return False, reason
        return True, ""

    # date
    if isinstance(a, date) and isinstance(b, date):
        ok = a == b
        return ok, "" if ok else f"col={col} row={idx}: date {a!r} vs {b!r}"

    # datetime
    if isinstance(a, datetime) and isinstance(b, datetime):
        # Compare at microsecond resolution
        a_us = int(a.timestamp() * 1_000_000)
        b_us = int(b.timestamp() * 1_000_000)
        ok = a_us == b_us
        return ok, "" if ok else f"col={col} row={idx}: datetime diff {abs(a_us-b_us)} us"

    # bytes
    if isinstance(a, bytes) and isinstance(b, bytes):
        ok = a == b
        return ok, "" if ok else f"col={col} row={idx}: bytes differ"

    # default
    ok = a == b
    return ok, "" if ok else f"col={col} row={idx}: {a!r} vs {b!r}"


# ---------------------------------------------------------------------------
# Column-specific read-back converters
# (from Arrow PyDict values → comparable Python values)
# ---------------------------------------------------------------------------

def _normalize_readback(col: str, arrow_val: Any) -> Any:
    """Convert Arrow read-back value to Python type for comparison."""
    if arrow_val is None:
        return None

    # date32 → date
    if isinstance(arrow_val, int) and "date" in col and "ts" not in col and "time" not in col:
        return _EPOCH_DATE + timedelta(days=arrow_val)

    # JSON string variants → parsed Python object (for comparison against source)
    if col in VARIANT_COLS or col.startswith("col_always_null_variant"):
        if isinstance(arrow_val, str):
            try:
                return json.loads(arrow_val)
            except (json.JSONDecodeError, TypeError):
                return arrow_val

    return arrow_val


def _normalize_source(col: str, raw_val: Any, arrow_val: Any) -> Any:
    """
    Normalize source (generator) value to match what we'd expect after
    write → read-back round-trip.
    """
    if raw_val is None:
        return None

    # For VARIANT columns: source is dict/list, read-back is parsed JSON
    # → compare as parsed JSON (no-op if both become dict/list)
    if col in VARIANT_COLS:
        if isinstance(raw_val, (dict, list)):
            # JSON round-trip may lose Decimal precision in variant
            return json.loads(json.dumps(raw_val, default=str))

    # datetime → microsecond-truncated (arrow stores us)
    if isinstance(raw_val, datetime) and "ts" in col:
        return raw_val  # compared via _eq datetime path

    return raw_val


# ---------------------------------------------------------------------------
# Main verifier
# ---------------------------------------------------------------------------

class ParityResult:
    def __init__(self):
        self.errors: list[str] = []
        self.checked_rows = 0
        self.checked_cells = 0

    @property
    def ok(self): return len(self.errors) == 0

    def summary(self) -> str:
        status = "PASS ✓" if self.ok else f"FAIL ✗ ({len(self.errors)} errors)"
        return (f"{status} | rows={self.checked_rows} "
                f"cells={self.checked_cells} errors={len(self.errors)}")

    def __repr__(self): return self.summary()


def verify_parity(source_rows: list[dict], parquet_path: str | Path,
                  max_errors: int = 20) -> ParityResult:
    """
    Read the Parquet file back and compare every cell against source_rows.

    Args:
        source_rows:   original Python dicts (from generator or Snowflake)
        parquet_path:  path to the Parquet file to verify
        max_errors:    stop collecting errors after this many

    Returns:
        ParityResult with error details
    """
    result = ParityResult()

    table = pq.read_table(str(parquet_path))
    assert len(table) == len(source_rows), (
        f"Row count mismatch: parquet={len(table)} source={len(source_rows)}")

    pydict = table.to_pydict()
    col_names = [f.name for f in SCHEMA]

    result.checked_rows = len(source_rows)

    for col in col_names:
        arrow_col = pydict[col]
        for i, (arrow_val, src_row) in enumerate(zip(arrow_col, source_rows)):
            src_val = src_row.get(col)

            # Normalize both sides for comparison
            rb  = _normalize_readback(col, arrow_val)
            src = _normalize_source(col, src_val, arrow_val)

            ok, reason = _eq(src, rb, col=col, idx=i)
            result.checked_cells += 1

            if not ok:
                result.errors.append(reason)
                if len(result.errors) >= max_errors:
                    return result

    return result


def compare_tables(table_a: pa.Table, table_b: pa.Table,
                   label_a: str = "A", label_b: str = "B") -> ParityResult:
    """
    Compare two pa.Tables cell-by-cell (e.g., Approach A vs Approach B output).
    """
    result = ParityResult()
    assert len(table_a) == len(table_b), "Row count mismatch"

    dict_a = table_a.to_pydict()
    dict_b = table_b.to_pydict()
    col_names = [f.name for f in SCHEMA]
    result.checked_rows = len(table_a)

    for col in col_names:
        col_a = dict_a[col]
        col_b = dict_b[col]
        for i, (va, vb) in enumerate(zip(col_a, col_b)):
            ok, reason = _eq(va, vb, col=f"{label_a}vs{label_b}/{col}", idx=i)
            result.checked_cells += 1
            if not ok:
                result.errors.append(reason)

    return result
