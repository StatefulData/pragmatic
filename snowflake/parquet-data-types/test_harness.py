"""
sf_parquet_harness_v3/tests/test_harness.py

Comprehensive pytest suite for Snowflake → PyArrow → Parquet conversion.
Tests: schema, primitive types, date/time/timestamp, binary, variant,
       structs, typed arrays, array of struct, map/dict variants,
       null matrix, parity verification, Approach A vs B comparison.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# Add parent dir to path when running directly
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.generator import generate_rows, make_row
from src.schema import (
    SCHEMA, USER_STRUCT, GEO_STRUCT, EVENT_STRUCT,
    PRIMITIVE_COLS, DATETIME_COLS, BINARY_COLS, VARIANT_COLS,
    STRUCT_COLS, ARRAY_COLS, MAP_COLS, ALWAYS_NULL_COLS,
)
from src.converters import (
    to_date32, to_ts_ms, to_ts_us, to_ts_ns, to_decimal,
    variant_to_json, convert_user_struct, convert_geo_struct,
    convert_event_struct, convert_arr_int, convert_arr_double,
    convert_arr_decimal, convert_arr_date, convert_arr_ts,
    convert_arr_bool, convert_arr_string, convert_arr_struct,
    convert_map_str_int, convert_map_str_double,
    convert_map_str_string, convert_map_str_bool, convert_map_str_decimal,
)
from src.writer_approach_a import write_parquet_approach_a
from src.writer_approach_b import write_parquet_approach_b
from src.verifier import verify_parity, compare_tables, ParityResult


# ============================================================================
# Fixtures
# ============================================================================

N_ROWS = 500  # smaller for CI speed; use 2000 for full validation

@pytest.fixture(scope="session")
def rows():
    return generate_rows(N_ROWS)

@pytest.fixture(scope="session")
def null_row():
    return make_row(50)  # rn=50 → is_null_row=True

@pytest.fixture(scope="session")
def boundary_rows():
    return [make_row(1), make_row(N_ROWS)]  # min / max

@pytest.fixture(scope="session")
def output_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("parquet_v3")

@pytest.fixture(scope="session")
def parquet_b(rows, output_dir):
    path = output_dir / "approach_b.parquet"
    stats = write_parquet_approach_b(rows, str(path))
    return path, stats

@pytest.fixture(scope="session")
def parquet_a(rows, output_dir):
    path = output_dir / "approach_a.parquet"
    stats = write_parquet_approach_a(rows, str(path))
    return path, stats

@pytest.fixture(scope="session")
def table_b(parquet_b):
    return pq.read_table(str(parquet_b[0]))

@pytest.fixture(scope="session")
def table_a(parquet_a):
    return pq.read_table(str(parquet_a[0]))


# ============================================================================
# 1. Schema Tests
# ============================================================================

class TestSchema:
    def test_schema_field_count(self):
        assert len(SCHEMA) == 41

    def test_schema_has_metadata(self):
        assert b"source" in SCHEMA.metadata
        assert SCHEMA.metadata[b"source"] == b"snowflake"

    def test_all_fields_have_sf_type_metadata(self):
        for field in SCHEMA:
            assert field.metadata is not None, f"{field.name} has no metadata"
            assert b"sf_type" in field.metadata, f"{field.name} missing sf_type"

    def test_decimal_precision_scale(self):
        f10_4  = SCHEMA.field("col_decimal_10_4").type
        f38_12 = SCHEMA.field("col_decimal_38_12").type
        assert isinstance(f10_4,  pa.Decimal128Type)
        assert f10_4.precision == 10 and f10_4.scale == 4
        assert isinstance(f38_12, pa.Decimal128Type)
        assert f38_12.precision == 38 and f38_12.scale == 12

    def test_timestamp_units(self):
        assert SCHEMA.field("col_ts_ms").type  == pa.timestamp("ms")
        assert SCHEMA.field("col_ts_us").type  == pa.timestamp("us")
        assert SCHEMA.field("col_ts_ns").type  == pa.timestamp("ns")
        assert SCHEMA.field("col_ts_ltz").type == pa.timestamp("us", tz="UTC")

    def test_list_element_types(self):
        assert SCHEMA.field("col_arr_int").type    == pa.list_(pa.int64())
        assert SCHEMA.field("col_arr_double").type == pa.list_(pa.float64())
        assert SCHEMA.field("col_arr_date").type   == pa.list_(pa.date32())
        assert SCHEMA.field("col_arr_bool").type   == pa.list_(pa.bool_())
        assert SCHEMA.field("col_arr_ts").type     == pa.list_(pa.timestamp("us"))
        assert SCHEMA.field("col_arr_decimal").type == pa.list_(pa.decimal128(10, 4))

    def test_array_of_struct_type(self):
        t = SCHEMA.field("col_arr_struct").type
        assert isinstance(t, pa.ListType)
        assert isinstance(t.value_type, pa.StructType)

    def test_map_types(self):
        assert SCHEMA.field("col_map_str_int").type    == pa.map_(pa.string(), pa.int64())
        assert SCHEMA.field("col_map_str_double").type == pa.map_(pa.string(), pa.float64())
        assert SCHEMA.field("col_map_str_string").type == pa.map_(pa.string(), pa.string())
        assert SCHEMA.field("col_map_str_bool").type   == pa.map_(pa.string(), pa.bool_())
        assert SCHEMA.field("col_map_str_decimal").type == pa.map_(pa.string(), pa.decimal128(12, 4))

    def test_named_struct_fields(self):
        user = SCHEMA.field("col_struct_user").type
        assert isinstance(user, pa.StructType)
        assert user.field("user_id").type   == pa.int32()
        assert user.field("score").type     == pa.decimal128(10, 4)
        assert user.field("is_active").type == pa.bool_()

    def test_variant_stored_as_string(self):
        """VARIANT must be pa.string(), NOT pa.struct() or custom binary."""
        assert SCHEMA.field("col_variant").type == pa.string()
        assert SCHEMA.field("col_variant_array").type == pa.string()
        assert SCHEMA.field("col_always_null_variant").type == pa.string()


# ============================================================================
# 2. Converter Unit Tests
# ============================================================================

class TestConverters:
    def test_to_date32_epoch(self):
        assert to_date32(date(1970, 1, 1)) == 0

    def test_to_date32_positive(self):
        assert to_date32(date(1970, 1, 2)) == 1

    def test_to_date32_none(self):
        assert to_date32(None) is None

    def test_to_ts_us_epoch(self):
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert to_ts_us(dt) == 0

    def test_to_ts_us_positive(self):
        dt = datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        assert to_ts_us(dt) == 1_000_000

    def test_to_ts_ms_integer_math(self):
        """Timestamps must use integer arithmetic to avoid float precision loss."""
        dt = datetime(2020, 6, 15, 12, 30, 45, 123000)
        ms = to_ts_ms(dt)
        assert isinstance(ms, int)

    def test_to_ts_ns_integer_math(self):
        dt = datetime(2020, 1, 1)
        ns = to_ts_ns(dt)
        assert isinstance(ns, int)
        assert ns % 1000 == 0  # ns from datetime is always multiple of 1000

    def test_decimal_scale(self):
        d = to_decimal(1.5, 10, 4)
        assert isinstance(d, Decimal)
        assert str(d) == "1.5000"

    def test_decimal_38_12(self):
        d = to_decimal("123456.789012345678", 38, 12)
        assert isinstance(d, Decimal)
        assert d.as_tuple().exponent == -12

    def test_variant_to_json_dict(self):
        v = {"id": 1, "tags": ["a"]}
        s = variant_to_json(v)
        assert isinstance(s, str)
        assert json.loads(s) == v

    def test_variant_to_json_none(self):
        assert variant_to_json(None) is None

    def test_variant_to_json_already_string(self):
        s = '{"x": 1}'
        assert variant_to_json(s) == s

    def test_convert_user_struct(self):
        raw = {"user_id": 1, "username": "alice", "score": Decimal("1.5"),
               "is_active": True, "signup_date": "2010-01-02"}
        result = convert_user_struct(raw)
        assert result["user_id"] == 1
        assert isinstance(result["score"], Decimal)
        assert result["is_active"] is True

    def test_convert_event_struct(self):
        raw = {"event_id": 42, "event_ts": "2024-01-01 00:00:01.000000",
               "value": Decimal("0.42")}
        result = convert_event_struct(raw)
        assert result["event_id"] == 42
        assert isinstance(result["value"], Decimal)

    def test_convert_arr_date(self):
        result = convert_arr_date(["2023-01-01", "2023-01-02", None])
        assert result[0] == to_date32(date(2023, 1, 1))
        assert result[1] == to_date32(date(2023, 1, 2))
        assert result[2] is None

    def test_convert_arr_decimal(self):
        result = convert_arr_decimal([1.5, 2.0, None], 10, 4)
        assert isinstance(result[0], Decimal)
        assert result[2] is None

    def test_convert_map_str_int_returns_sorted_tuples(self):
        d = {"b": 2, "a": 1}
        result = convert_map_str_int(d)
        assert result == [("a", 1), ("b", 2)]

    def test_convert_map_str_decimal(self):
        d = {"price": Decimal("9.99"), "tax": Decimal("0.08")}
        result = convert_map_str_decimal(d, 12, 4)
        assert all(isinstance(v, Decimal) for _, v in result)

    def test_convert_map_none(self):
        assert convert_map_str_int(None) is None


# ============================================================================
# 3. Generator Tests
# ============================================================================

class TestGenerator:
    def test_row_count(self, rows):
        assert len(rows) == N_ROWS

    def test_null_rows_present(self, rows):
        null_rows = [r for r in rows if r["is_null_row"]]
        assert len(null_rows) > 0

    def test_null_row_has_none_values(self, null_row):
        assert null_row["is_null_row"] is True
        for col in PRIMITIVE_COLS + DATETIME_COLS + BINARY_COLS + VARIANT_COLS:
            assert null_row[col] is None, f"{col} should be None in null row"

    def test_always_null_cols_are_always_none(self, rows):
        for col in ALWAYS_NULL_COLS:
            for r in rows:
                assert r[col] is None, f"{col} row {r['row_id']} not None"

    def test_variant_is_dict_or_list(self, rows):
        non_null = [r for r in rows if r["col_variant"] is not None]
        for r in non_null:
            assert isinstance(r["col_variant"], dict)
            assert isinstance(r["col_variant_array"], list)

    def test_arrays_have_correct_length(self, rows):
        non_null = [r for r in rows if r["col_arr_int"] is not None]
        for r in non_null:
            assert len(r["col_arr_int"]) == 4
            assert len(r["col_arr_double"]) == 3
            assert len(r["col_arr_bool"]) == 3
            assert len(r["col_arr_string"]) == 2
            assert len(r["col_arr_struct"]) == 2

    def test_map_keys_present(self, rows):
        non_null = [r for r in rows if r["col_map_str_int"] is not None]
        for r in non_null:
            assert set(r["col_map_str_int"].keys()) == {"count", "total", "max"}
            assert set(r["col_map_str_bool"].keys()) == {"enabled", "archived", "verified"}


# ============================================================================
# 4. Parquet Write Tests (Approach B)
# ============================================================================

class TestWriteApproachB:
    def test_file_exists(self, parquet_b):
        path, _ = parquet_b
        assert path.exists()

    def test_file_size_nonzero(self, parquet_b):
        path, stats = parquet_b
        assert stats["file_bytes"] > 0

    def test_row_count(self, table_b, rows):
        assert len(table_b) == len(rows)

    def test_schema_matches(self, table_b):
        # Compare field names and types (not metadata)
        for field in SCHEMA:
            rb_field = table_b.schema.field(field.name)
            assert rb_field.type == field.type, \
                f"{field.name}: expected {field.type}, got {rb_field.type}"

    def test_null_count_in_null_rows(self, table_b, rows):
        """Rows where is_null_row=True should have nulls in all nullable cols."""
        d = table_b.to_pydict()
        null_indices = [i for i, r in enumerate(rows) if r["is_null_row"]]
        for col in PRIMITIVE_COLS[:4]:  # spot-check first 4 primitives
            for i in null_indices:
                assert d[col][i] is None, f"{col}[{i}] should be null"

    def test_always_null_cols(self, table_b):
        d = table_b.to_pydict()
        for col in ALWAYS_NULL_COLS:
            assert all(v is None for v in d[col]), f"{col} has non-null values"

    def test_decimal_10_4_type(self, table_b):
        col = table_b.column("col_decimal_10_4")
        assert col.type == pa.decimal128(10, 4)

    def test_decimal_38_12_type(self, table_b):
        col = table_b.column("col_decimal_38_12")
        assert col.type == pa.decimal128(38, 12)

    def test_timestamp_ms(self, table_b):
        assert table_b.column("col_ts_ms").type == pa.timestamp("ms")

    def test_timestamp_ns(self, table_b):
        assert table_b.column("col_ts_ns").type == pa.timestamp("ns")

    def test_timestamp_ltz_utc(self, table_b):
        assert table_b.column("col_ts_ltz").type == pa.timestamp("us", tz="UTC")

    def test_date32(self, table_b):
        assert table_b.column("col_date").type == pa.date32()

    def test_time64_us(self, table_b):
        assert table_b.column("col_time_us").type == pa.time64("us")

    def test_binary(self, table_b):
        assert table_b.column("col_binary").type == pa.binary()

    def test_variant_is_string(self, table_b):
        for col in VARIANT_COLS:
            assert table_b.column(col).type == pa.string(), \
                f"{col} should be pa.string() (JSON), not {table_b.column(col).type}"

    def test_variant_is_valid_json(self, table_b, rows):
        d = table_b.to_pydict()
        non_null = [(i, v) for i, v in enumerate(d["col_variant"]) if v is not None]
        for i, v in non_null[:10]:  # spot check 10
            parsed = json.loads(v)
            assert isinstance(parsed, dict)
            assert "id" in parsed

    def test_named_struct_user(self, table_b):
        assert table_b.column("col_struct_user").type == USER_STRUCT

    def test_named_struct_geo(self, table_b):
        assert table_b.column("col_struct_geo").type == GEO_STRUCT

    def test_array_of_struct_type(self, table_b):
        t = table_b.column("col_arr_struct").type
        assert isinstance(t, pa.ListType)
        assert t.value_type == EVENT_STRUCT

    def test_arr_int_element_type(self, table_b):
        assert table_b.column("col_arr_int").type == pa.list_(pa.int64())

    def test_arr_decimal_element_type(self, table_b):
        assert table_b.column("col_arr_decimal").type == pa.list_(pa.decimal128(10, 4))

    def test_arr_date_element_type(self, table_b):
        assert table_b.column("col_arr_date").type == pa.list_(pa.date32())

    def test_arr_ts_element_type(self, table_b):
        assert table_b.column("col_arr_ts").type == pa.list_(pa.timestamp("us"))

    def test_map_str_int_type(self, table_b):
        assert table_b.column("col_map_str_int").type == pa.map_(pa.string(), pa.int64())

    def test_map_str_decimal_type(self, table_b):
        assert table_b.column("col_map_str_decimal").type == \
               pa.map_(pa.string(), pa.decimal128(12, 4))

    def test_map_str_bool_type(self, table_b):
        assert table_b.column("col_map_str_bool").type == pa.map_(pa.string(), pa.bool_())


# ============================================================================
# 5. Parquet Write Tests (Approach A)
# ============================================================================

class TestWriteApproachA:
    def test_file_exists(self, parquet_a):
        path, _ = parquet_a
        assert path.exists()

    def test_row_count(self, table_a, rows):
        assert len(table_a) == len(rows)

    def test_schema_matches(self, table_a):
        for field in SCHEMA:
            rb = table_a.schema.field(field.name)
            assert rb.type == field.type, \
                f"{field.name}: expected {field.type}, got {rb.type}"

    def test_variant_is_string(self, table_a):
        for col in VARIANT_COLS:
            assert table_a.column(col).type == pa.string()

    def test_decimal_types(self, table_a):
        assert table_a.column("col_decimal_10_4").type  == pa.decimal128(10, 4)
        assert table_a.column("col_decimal_38_12").type == pa.decimal128(38, 12)


# ============================================================================
# 6. Data Parity Tests
# ============================================================================

class TestDataParityApproachB:
    def test_parity_primitives(self, parquet_b, rows):
        """Spot-check primitive column parity."""
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:20]:
            i = r["row_id"] - 1
            assert d["col_bool"][i]   == r["col_bool"]
            assert d["col_int32"][i]  == r["col_int32"]
            assert d["col_int64"][i]  == r["col_int64"]
            assert d["col_varchar"][i] == r["col_varchar"]

    def test_parity_binary(self, parquet_b, rows):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:10]:
            i = r["row_id"] - 1
            assert d["col_binary"][i] == r["col_binary"]

    def test_parity_date(self, parquet_b, rows):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        _EPOCH = date(1970, 1, 1)
        for r in non_null[:10]:
            i = r["row_id"] - 1
            expected = r["col_date"]
            actual_days = d["col_date"][i]
            actual_date = _EPOCH + timedelta(days=actual_days) if isinstance(actual_days, int) else actual_days
            assert actual_date == expected

    def test_parity_arr_int(self, parquet_b, rows):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:10]:
            i = r["row_id"] - 1
            rb = d["col_arr_int"][i]
            src = r["col_arr_int"]
            assert list(rb) == src, f"row {r['row_id']}: {rb} vs {src}"

    def test_parity_arr_string(self, parquet_b, rows):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:10]:
            i = r["row_id"] - 1
            assert list(d["col_arr_string"][i]) == r["col_arr_string"]

    def test_parity_null_rows(self, parquet_b, rows):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        null_indices = [i for i, r in enumerate(rows) if r["is_null_row"]]
        assert len(null_indices) > 0
        spot_cols = ["col_bool", "col_int32", "col_varchar", "col_date",
                     "col_arr_int", "col_struct_user", "col_map_str_int"]
        for col in spot_cols:
            for i in null_indices:
                assert d[col][i] is None, f"{col}[{i}] should be None"

    def test_parity_always_null(self, parquet_b):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        for col in ALWAYS_NULL_COLS:
            assert all(v is None for v in d[col])

    def test_parity_map_str_int(self, parquet_b, rows):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:5]:
            i = r["row_id"] - 1
            rb_map = dict(d["col_map_str_int"][i])  # Arrow map → dict
            src = r["col_map_str_int"]
            for k, v in src.items():
                assert rb_map[k] == int(v), f"key {k}: {rb_map[k]} vs {v}"

    def test_parity_struct_user_fields(self, parquet_b, rows):
        path, _ = parquet_b
        t = pq.read_table(str(path))
        d = t.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:5]:
            i = r["row_id"] - 1
            rb = d["col_struct_user"][i]
            src = r["col_struct_user"]
            assert rb["user_id"] == src["user_id"]
            assert rb["username"] == src["username"]
            assert rb["is_active"] == src["is_active"]


# ============================================================================
# 7. Approach A vs B Cross-Validation
# ============================================================================

class TestApproachComparison:
    def test_same_row_count(self, table_a, table_b):
        assert len(table_a) == len(table_b)

    def test_same_schema_types(self, table_a, table_b):
        for field in SCHEMA:
            type_a = table_a.schema.field(field.name).type
            type_b = table_b.schema.field(field.name).type
            assert type_a == type_b, \
                f"{field.name}: A={type_a} B={type_b}"

    def test_primitive_columns_match(self, table_a, table_b):
        da = table_a.to_pydict()
        db = table_b.to_pydict()
        for col in ["col_bool", "col_int32", "col_int64", "col_varchar"]:
            assert da[col] == db[col], f"{col} differs between A and B"

    def test_decimal_columns_match(self, table_a, table_b):
        da = table_a.to_pydict()
        db = table_b.to_pydict()
        for col in ["col_decimal_10_4", "col_decimal_38_12"]:
            for i, (va, vb) in enumerate(zip(da[col], db[col])):
                assert va == vb, f"{col}[{i}]: A={va} B={vb}"

    def test_variant_json_matches(self, table_a, table_b):
        da = table_a.to_pydict()
        db = table_b.to_pydict()
        for col in VARIANT_COLS:
            for i, (va, vb) in enumerate(zip(da[col], db[col])):
                if va is None and vb is None:
                    continue
                assert json.loads(va) == json.loads(vb), \
                    f"{col}[{i}]: A JSON != B JSON"

    def test_null_rows_match(self, table_a, table_b, rows):
        da = table_a.to_pydict()
        db = table_b.to_pydict()
        null_idx = [i for i, r in enumerate(rows) if r["is_null_row"]]
        for col in PRIMITIVE_COLS[:3]:
            for i in null_idx:
                assert da[col][i] is None
                assert db[col][i] is None

    def test_timing_stats_exist(self, parquet_a, parquet_b):
        _, stats_a = parquet_a
        _, stats_b = parquet_b
        assert stats_a["approach"] == "A"
        assert stats_b["approach"] == "B"
        assert stats_a["total_sec"] > 0
        assert stats_b["total_sec"] > 0
        print(f"\nApproach A: {stats_a['total_sec']:.3f}s")
        print(f"Approach B: {stats_b['total_sec']:.3f}s")


# ============================================================================
# 8. NULL Matrix Tests
# ============================================================================

class TestNullMatrix:
    def test_top_level_null(self, table_b, rows):
        """Verify null at the top-level column for null rows."""
        d = table_b.to_pydict()
        null_idx = [i for i, r in enumerate(rows) if r["is_null_row"]]
        all_nullable = (PRIMITIVE_COLS + DATETIME_COLS + BINARY_COLS +
                        VARIANT_COLS + STRUCT_COLS + ARRAY_COLS + MAP_COLS)
        for col in all_nullable:
            for i in null_idx:
                assert d[col][i] is None, f"{col}[{i}] should be null"

    def test_empty_array_vs_null(self, rows):
        """Distinguish null array from empty array."""
        # In our generator, null rows have None, not []
        null_row = next(r for r in rows if r["is_null_row"])
        assert null_row["col_arr_int"] is None  # null, not []

    def test_always_null_columns(self, table_b):
        d = table_b.to_pydict()
        for col in ALWAYS_NULL_COLS:
            vals = d[col]
            assert all(v is None for v in vals), \
                f"{col} has {sum(1 for v in vals if v is not None)} non-null values"

    def test_null_count_metadata(self, parquet_b):
        """Parquet statistics should record null counts."""
        path, _ = parquet_b
        pf = pq.ParquetFile(str(path))
        rg = pf.metadata.row_group(0)
        # Check a known always-null column has nonzero null count
        for i in range(rg.num_columns):
            col = rg.column(i)
            if col.path_in_schema == "col_always_null_int":
                assert col.statistics.null_count == rg.num_rows
                break


# ============================================================================
# 9. Boundary Value Tests
# ============================================================================

class TestBoundaryValues:
    def test_row_1_values(self, table_b):
        d = table_b.to_pydict()
        assert d["row_id"][0] == 1
        assert d["col_int32"][0] == 1
        assert d["col_varchar"][0] == "row_1"

    def test_large_decimal(self, table_b, rows):
        d = table_b.to_pydict()
        non_null = [i for i, r in enumerate(rows) if not r["is_null_row"]]
        # Just verify it's a Decimal and scale matches
        v = d["col_decimal_38_12"][non_null[0]]
        assert isinstance(v, Decimal)
        assert v.as_tuple().exponent == -12

    def test_float_precision(self, table_b, rows):
        d = table_b.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:5]:
            i = r["row_id"] - 1
            rb = d["col_double"][i]
            src = r["col_double"]
            assert abs(rb - src) < 1e-9, f"float precision loss: {rb} vs {src}"

    def test_binary_exact(self, table_b, rows):
        d = table_b.to_pydict()
        non_null = [r for r in rows if not r["is_null_row"]]
        for r in non_null[:10]:
            i = r["row_id"] - 1
            assert d["col_binary"][i] == r["col_binary"]
