"""
sf_parquet_harness_v3/src/generator.py

Synthetic data generator that mirrors the Snowflake SQL query output.
Used for local unit tests WITHOUT a Snowflake connection.

The generator produces Python dicts matching what the Snowflake connector
would return via cursor.fetchall(as_dict=True).
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

random.seed(42)

_EPOCH = date(1970, 1, 1)
_TS_EPOCH = datetime(2020, 1, 1)
_DATE_BASE = date(2000, 1, 1)
_ARR_DATE_BASE = date(2023, 1, 1)
_ARR_TS_BASE = datetime(2022, 1, 1)
_SIGNUP_BASE = date(2010, 1, 1)
_EVENT_TS_BASE = datetime(2024, 1, 1)


def _maybe_null(rn: int, v: Any) -> Any:
    """Return None for null rows (rn % 50 == 0), else v."""
    return None if rn % 50 == 0 else v


def make_row(rn: int) -> dict:
    """Generate one synthetic row matching the SQL output schema."""
    is_null = (rn % 50 == 0)
    N = None  # shorthand

    def n(v):
        return None if is_null else v

    # --- VARIANT dicts (simulating what Snowflake JSON-parses to) ---
    variant_obj = n({
        "id": rn,
        "score": round(rn * 0.5, 6),
        "tags": ["a", "b"],
        "nested": {"x": rn},
    })
    variant_arr = n([rn, f"str_{rn}", True, None])

    struct_user = n({
        "user_id":     rn,
        "username":    f"user_{rn}",
        "score":       Decimal(str(round(rn * 1.5, 4))),
        "is_active":   (rn % 3 != 0),
        "signup_date": (_SIGNUP_BASE + timedelta(days=rn)).isoformat(),
    })
    struct_geo = n({
        "lat":        Decimal(str(round((rn % 90 - 45), 6))),
        "lon":        Decimal(str(round((rn % 180 - 90), 6))),
        "altitude_m": rn * 10,
        "label":      f"loc_{rn}",
    })

    arr_date_base = _ARR_DATE_BASE + timedelta(days=rn)
    arr_ts_us_base = _ARR_TS_BASE + timedelta(microseconds=rn * 1000)

    arr_struct = n([
        {"event_id": rn,      "event_ts": (_EVENT_TS_BASE + timedelta(seconds=rn)).strftime("%Y-%m-%d %H:%M:%S.%f"),   "value": Decimal(str(round(rn * 0.01, 4)))},
        {"event_id": rn+1000, "event_ts": (_EVENT_TS_BASE + timedelta(seconds=rn+1)).strftime("%Y-%m-%d %H:%M:%S.%f"), "value": Decimal(str(round((rn+1) * 0.01, 4)))},
    ])

    ts_ms   = n(_TS_EPOCH + timedelta(milliseconds=rn * 1000))
    ts_us   = n(_TS_EPOCH + timedelta(microseconds=rn * 1000))
    ts_ns   = n(_TS_EPOCH + timedelta(microseconds=rn))   # ns precision limited by datetime
    ts_ltz  = n(datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=rn))

    return {
        "row_id":       rn,
        "is_null_row":  is_null,

        # primitives
        "col_bool":     n(rn % 2 == 0),
        "col_int16":    n(rn),
        "col_int32":    n(rn),
        "col_int64":    n(rn),
        "col_decimal_10_4":  n(Decimal(str(round(rn * 1.5, 4)))),
        "col_decimal_38_12": n(Decimal(str(round(rn * 123456.789012345678, 12)))),
        "col_double":   n(rn * 3.14159265358979),
        "col_float":    n(1.0 / rn),
        "col_varchar":  n(f"row_{rn}"),
        "col_text":     n("text_" + ("abcdefghij" * 51)[:min(rn, 500)]),

        # date/time/timestamp
        "col_date":     n(_DATE_BASE + timedelta(days=rn)),
        "col_time_us":  n(rn % 86400 * 1_000_000),   # microseconds since midnight
        "col_ts_ms":    ts_ms,
        "col_ts_us":    ts_us,
        "col_ts_ns":    ts_ns,
        "col_ts_ltz":   ts_ltz,

        # binary
        "col_binary":   n(rn.to_bytes(4, "big")),

        # variant
        "col_variant":       variant_obj,
        "col_variant_array": variant_arr,

        # structs
        "col_struct_user":   struct_user,
        "col_struct_geo":    struct_geo,

        # typed arrays
        "col_arr_int":    n([rn, rn+1, rn+2, rn+3]),
        "col_arr_double": n([rn * 1.1, rn * 2.2, rn * 3.3]),
        "col_arr_decimal": n([
            Decimal(str(round(rn * 0.0001, 4))),
            Decimal(str(round(rn * 0.0002, 4))),
            Decimal(str(round(rn * 0.0003, 4))),
        ]),
        "col_arr_date": n([
            (arr_date_base).isoformat(),
            (arr_date_base + timedelta(days=1)).isoformat(),
            (arr_date_base + timedelta(days=2)).isoformat(),
        ]),
        "col_arr_ts": n([
            arr_ts_us_base.strftime("%Y-%m-%d %H:%M:%S.%f"),
            (arr_ts_us_base + timedelta(microseconds=rn * 1000)).strftime("%Y-%m-%d %H:%M:%S.%f"),
        ]),
        "col_arr_bool":   n([rn % 2 == 0, rn % 3 == 0, rn % 5 == 0]),
        "col_arr_string": n([f"item_{rn}", f"item_{rn+100}"]),
        "col_arr_struct": arr_struct,

        # maps
        "col_map_str_int": n({"count": rn, "total": rn * 10, "max": rn + 100}),
        "col_map_str_double": n({"p50": rn * 0.5, "p95": rn * 0.95, "p99": rn * 0.99}),
        "col_map_str_string": n({"env": "prod", "region": f"us-west-{rn % 3 + 1}", "tier": "premium" if rn % 2 == 0 else "standard"}),
        "col_map_str_bool": n({"enabled": rn % 2 == 0, "archived": rn % 7 == 0, "verified": rn % 3 == 0}),
        "col_map_str_decimal": n({
            "price":    Decimal(str(round(rn * 9.99, 4))),
            "tax":      Decimal(str(round(rn * 0.08, 4))),
            "discount": Decimal(str(round(rn * 0.05, 4))),
        }),

        # always-null
        "col_always_null_int":     None,
        "col_always_null_str":     None,
        "col_always_null_ts":      None,
        "col_always_null_variant": None,
        "col_always_null_bool":    None,
    }


def generate_rows(n: int = 2000) -> list[dict]:
    """Generate n synthetic rows (rn starts at 1)."""
    return [make_row(rn) for rn in range(1, n + 1)]
