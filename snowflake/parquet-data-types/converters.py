"""
sf_parquet_harness_v3/src/converters.py

Type-safe value converters: Snowflake Python connector output → Arrow-compatible Python values.

Design rules:
  - All timestamp/date conversions use integer arithmetic (never float).
  - All Decimal conversions use the Python `decimal` module.
  - VARIANT/OBJECT/ARRAY from Snowflake arrive as Python dicts/lists (connector parses JSON).
  - MAP columns: output is list-of-(key, value) tuples for pa.map_() arrays.
  - Struct columns: output is dict with exactly the fields matching pa.struct definition order.
  - None input → None output (Arrow handles null bitmap).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import pyarrow as pa

# ---------------------------------------------------------------------------
# Epoch reference
# ---------------------------------------------------------------------------
_EPOCH_DATE = date(1970, 1, 1)
_EPOCH_DT   = datetime(1970, 1, 1, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Scalar converters
# ---------------------------------------------------------------------------

def to_date32(v: Optional[date]) -> Optional[int]:
    """date → int32 (days since epoch) for pa.date32()."""
    if v is None:
        return None
    return (v - _EPOCH_DATE).days


def from_date32(days: Optional[int]) -> Optional[date]:
    """int32 days → date."""
    if days is None:
        return None
    return _EPOCH_DATE + timedelta(days=days)


def to_ts_ms(v: Optional[datetime]) -> Optional[int]:
    """datetime → int64 milliseconds since epoch for pa.timestamp('ms')."""
    if v is None:
        return None
    # Use timedelta arithmetic to stay in integer domain
    delta = v.replace(tzinfo=timezone.utc) - _EPOCH_DT if v.tzinfo is None else v - _EPOCH_DT
    return int(delta.total_seconds() * 1_000)


def to_ts_us(v: Optional[datetime]) -> Optional[int]:
    """datetime → int64 microseconds since epoch for pa.timestamp('us')."""
    if v is None:
        return None
    delta = v.replace(tzinfo=timezone.utc) - _EPOCH_DT if v.tzinfo is None else v - _EPOCH_DT
    total_us = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    return total_us


def to_ts_ns(v: Optional[datetime]) -> Optional[int]:
    """datetime → int64 nanoseconds since epoch for pa.timestamp('ns').
    
    NOTE: Python datetime has microsecond resolution. We multiply by 1000
    to get nanoseconds; sub-microsecond ns precision is lost (expected).
    """
    if v is None:
        return None
    us = to_ts_us(v)
    return us * 1_000  # nanoseconds


def to_decimal(v: Any, precision: int, scale: int) -> Optional[Decimal]:
    """Convert numeric value to Decimal with given precision/scale."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v.quantize(Decimal(10) ** -scale, rounding=ROUND_HALF_UP)
    return Decimal(str(v)).quantize(Decimal(10) ** -scale, rounding=ROUND_HALF_UP)


def variant_to_json(v: Any) -> Optional[str]:
    """VARIANT / OBJECT → JSON string for pa.string()."""
    if v is None:
        return None
    if isinstance(v, str):
        # Already JSON string (Approach A path where connector gives string)
        return v
    return json.dumps(v, default=str)


def json_to_python(s: Optional[str]) -> Any:
    """JSON string → Python dict/list/scalar (for read-back comparison)."""
    if s is None:
        return None
    return json.loads(s)


# ---------------------------------------------------------------------------
# Date / timestamp ISO string converters (used inside VARIANT arrays/structs)
# ---------------------------------------------------------------------------

def iso_str_to_date(s: Optional[str]) -> Optional[date]:
    """'YYYY-MM-DD' → date."""
    if s is None:
        return None
    return date.fromisoformat(s[:10])


def iso_str_to_ts_us(s: Optional[str]) -> Optional[int]:
    """ISO timestamp string → microseconds since epoch."""
    if s is None:
        return None
    # Handle various formats Snowflake may emit
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",    "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s[:26], fmt)
            return to_ts_us(dt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp string: {s!r}")


# ---------------------------------------------------------------------------
# Struct converters
# ---------------------------------------------------------------------------

def convert_user_struct(v: Optional[dict]) -> Optional[dict]:
    """Convert raw OBJECT dict to USER_STRUCT-compatible dict.
    
    Keys must match pa.struct field names exactly.
    Applies type coercion for Decimal and date fields.
    """
    if v is None:
        return None
    return {
        "user_id":     int(v["user_id"]) if v.get("user_id") is not None else None,
        "username":    str(v["username"]) if v.get("username") is not None else None,
        "score":       to_decimal(v.get("score"), 10, 4),
        "is_active":   bool(v["is_active"]) if v.get("is_active") is not None else None,
        "signup_date": str(v["signup_date"]) if v.get("signup_date") is not None else None,
    }


def convert_geo_struct(v: Optional[dict]) -> Optional[dict]:
    if v is None:
        return None
    return {
        "lat":        to_decimal(v.get("lat"), 8, 6),
        "lon":        to_decimal(v.get("lon"), 9, 6),
        "altitude_m": int(v["altitude_m"]) if v.get("altitude_m") is not None else None,
        "label":      str(v["label"]) if v.get("label") is not None else None,
    }


def convert_event_struct(v: Optional[dict]) -> Optional[dict]:
    if v is None:
        return None
    return {
        "event_id": int(v["event_id"]) if v.get("event_id") is not None else None,
        "event_ts": str(v["event_ts"]) if v.get("event_ts") is not None else None,
        "value":    to_decimal(v.get("value"), 12, 4),
    }


# ---------------------------------------------------------------------------
# Array converters — convert list of raw values to Arrow-ready list
# ---------------------------------------------------------------------------

def convert_arr_int(v: Optional[list]) -> Optional[list]:
    if v is None:
        return None
    return [int(x) if x is not None else None for x in v]


def convert_arr_double(v: Optional[list]) -> Optional[list]:
    if v is None:
        return None
    return [float(x) if x is not None else None for x in v]


def convert_arr_decimal(v: Optional[list], precision: int = 10, scale: int = 4) -> Optional[list]:
    if v is None:
        return None
    return [to_decimal(x, precision, scale) if x is not None else None for x in v]


def convert_arr_date(v: Optional[list]) -> Optional[list]:
    """List of ISO date strings → list of int32 (date32 days)."""
    if v is None:
        return None
    result = []
    for x in v:
        if x is None:
            result.append(None)
        elif isinstance(x, str):
            result.append(to_date32(iso_str_to_date(x)))
        elif isinstance(x, date):
            result.append(to_date32(x))
        else:
            result.append(None)
    return result


def convert_arr_ts(v: Optional[list]) -> Optional[list]:
    """List of ISO timestamp strings → list of int64 (us epoch)."""
    if v is None:
        return None
    result = []
    for x in v:
        if x is None:
            result.append(None)
        elif isinstance(x, str):
            result.append(iso_str_to_ts_us(x))
        elif isinstance(x, datetime):
            result.append(to_ts_us(x))
        else:
            result.append(None)
    return result


def convert_arr_bool(v: Optional[list]) -> Optional[list]:
    if v is None:
        return None
    return [bool(x) if x is not None else None for x in v]


def convert_arr_string(v: Optional[list]) -> Optional[list]:
    if v is None:
        return None
    return [str(x) if x is not None else None for x in v]


def convert_arr_struct(v: Optional[list], converter) -> Optional[list]:
    if v is None:
        return None
    return [converter(x) if x is not None else None for x in v]


# ---------------------------------------------------------------------------
# MAP converters — output list of (key, value) tuples for pa.map_()
# ---------------------------------------------------------------------------

def convert_map(v: Optional[dict], value_converter=None) -> Optional[list]:
    """dict → list of (key, value) tuples.
    
    Args:
        v: raw dict from Snowflake
        value_converter: optional callable(value) → converted value
    """
    if v is None:
        return None
    items = sorted(v.items())  # sort for deterministic comparison
    if value_converter is None:
        return [(k, val) for k, val in items]
    return [(k, value_converter(val) if val is not None else None) for k, val in items]


def convert_map_str_int(v: Optional[dict]) -> Optional[list]:
    return convert_map(v, lambda x: int(x))


def convert_map_str_double(v: Optional[dict]) -> Optional[list]:
    return convert_map(v, lambda x: float(x))


def convert_map_str_string(v: Optional[dict]) -> Optional[list]:
    return convert_map(v, lambda x: str(x))


def convert_map_str_bool(v: Optional[dict]) -> Optional[list]:
    return convert_map(v, lambda x: bool(x))


def convert_map_str_decimal(v: Optional[dict], precision: int = 12, scale: int = 4) -> Optional[list]:
    return convert_map(v, lambda x: to_decimal(x, precision, scale))
