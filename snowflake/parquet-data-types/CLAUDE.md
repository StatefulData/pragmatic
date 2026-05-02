# Snowflake → Python → PyArrow → Parquet Conversion Harness

- https://docs.snowflake.com/en/sql-reference-data-types
- https://arrow.apache.org/docs/python/api/datatypes.html
- https://github.com/apache/parquet-format#types
- https://github.com/apache/parquet-format/blob/master/LogicalTypes.md

## Local Test
` python ./snowflake_writer.py --account [org]-[account] --user foo --private-key-path /path/to/rsa_key.p8 --passphrase xxx`

## Accumulated Caveats & Lessons Learned

---

## PARQUET VARIANT STATUS (as of 2025)

**VARIANT cannot be stored as native Parquet VARIANT.**

- Apache Parquet spec has no VARIANT logical type (Iceberg REST Catalog v2 is exploring it but not landed in PyArrow).
- PyArrow ≥ 15 has an experimental `pa.large_binary()` shred for Iceberg VARIANT but it is NOT readable by standard Parquet consumers.
- **Correct approach**: serialize VARIANT → UTF-8 JSON string → `pa.string()` or `pa.large_string()`.
- On read-back, `json.loads()` to reconstruct Python dict/list.
- Never use `pa.struct()` to approximate VARIANT — it requires a fixed schema which VARIANT does not have.

---

## PRIMITIVE TYPE CAVEATS

### 1. TIMESTAMP precision must match Arrow exactly
- `TIMESTAMP_LTZ(9)` / `TIMESTAMP_NTZ(9)` → `pa.timestamp('ns')` (nanoseconds)
- `TIMESTAMP_LTZ(6)` / `TIMESTAMP_NTZ(6)` → `pa.timestamp('us')`
- `TIMESTAMP_LTZ(3)` / `TIMESTAMP_NTZ(3)` → `pa.timestamp('ms')`
- Snowflake returns timestamps as integers (epoch * 10^precision). **Use integer arithmetic, not float**, to avoid sub-microsecond precision loss.
- Converter: `epoch_ns = int_val * (10 ** (9 - sf_precision))` — always keep as int.

### 2. TIMESTAMP_TZ (with timezone) requires special handling
- Snowflake encodes TZ timestamps as a packed integer: `(epoch_ns_value, tz_offset_minutes)`.
- PyArrow `pa.timestamp('ns', tz='UTC')` stores UTC epoch; apply offset only for display.
- Safer: normalize all TZ timestamps to UTC on extract.

### 3. DATE → `pa.date32()`
- Snowflake DATE returns `datetime.date` objects via the Python connector.
- Convert: `(date_val - date(1970,1,1)).days` → int32 for Arrow `date32`.
- Do NOT use `pa.date64()` (milliseconds) unless you explicitly need it.

### 4. TIME → `pa.time64('us')` or `pa.time32('ms')`
- Snowflake TIME(6) → microseconds since midnight → `pa.time64('us')`.
- Snowflake TIME(3) → milliseconds since midnight → `pa.time32('ms')`.

### 5. NUMBER / DECIMAL precision
- `NUMBER(38, 0)` → `pa.int64()` (fits; use int64 not decimal for whole numbers when p≤18).
- `NUMBER(p, s)` where s>0 → `pa.decimal128(p, s)`. PyArrow requires `Decimal` Python objects.
- Never use `float` for DECIMAL — you will lose precision silently.
- `NUMBER(38, 12)` is the widest common case; always specify both precision and scale.

### 6. FLOAT / DOUBLE → `pa.float64()`
- Snowflake FLOAT = IEEE 754 double. Always map to `pa.float64()`, never `pa.float32()`.

### 7. BOOLEAN → `pa.bool_()`
- Snowflake returns Python `bool`. Direct passthrough; no conversion needed.

### 8. VARCHAR / TEXT → `pa.string()` (UTF-8)
- For large text (>2GB column), use `pa.large_string()`.

### 9. BINARY / VARBINARY → `pa.binary()`
- Snowflake connector returns `bytes`. Direct passthrough to `pa.binary()`.
- For fixed-width binary: `pa.binary(n)`.

### 10. VARIANT → `pa.string()` (JSON serialized)
- See PARQUET VARIANT STATUS above.
- Always `json.dumps(variant_val)` before writing; `json.loads()` on read.

---

## COMPLEX / NESTED TYPE CAVEATS

### 11. ARRAY types — must be strongly typed in Arrow
- Snowflake `ARRAY` is schema-less at the SQL level but the data has a consistent type.
- Arrow requires `pa.list_(element_type)` — you MUST specify the element type explicitly.
- Common mappings:
  - `ARRAY(NUMBER)` integer values → `pa.list_(pa.int64())`
  - `ARRAY(NUMBER(p,s))` with scale → `pa.list_(pa.decimal128(p,s))`
  - `ARRAY(DOUBLE)` → `pa.list_(pa.float64())`
  - `ARRAY(DATE)` → `pa.list_(pa.date32())`
  - `ARRAY(TIMESTAMP_NTZ(6))` → `pa.list_(pa.timestamp('us'))`
  - `ARRAY(VARCHAR)` → `pa.list_(pa.string())`
  - `ARRAY(BOOLEAN)` → `pa.list_(pa.bool_())`
- For large lists: `pa.large_list(element_type)`.
- **Snowflake returns ARRAY columns as Python lists** via the Python connector (JSON-parsed).
  Each element still needs the same type conversion as the scalar version.

### 12. ARRAY of STRUCT (named struct elements)
- Arrow: `pa.list_(pa.struct([pa.field('f1', t1), pa.field('f2', t2), ...]))`.
- Snowflake returns list of dicts; convert each dict to match field order.
- Field order in the dict MUST match the `pa.struct` field definition order.

### 13. OBJECT / STRUCT — named vs unnamed
- Snowflake `OBJECT` (schema-less) → treat like VARIANT → `pa.string()` (JSON).
- Snowflake named struct (e.g., from `OBJECT_CONSTRUCT` with known keys):
  - Arrow: `pa.struct([pa.field('key1', type1), pa.field('key2', type2)])`.
  - Elements arrive as Python dicts; extract fields in order.
  - Missing fields must be filled with `None` (Arrow will null them).

### 14. MAP / DICT
- Arrow `pa.map_(key_type, value_type)` — key is almost always `pa.string()`.
- Snowflake returns map columns as Python dicts.
- Arrow `map_` input = **list of (key, value) tuples**, NOT a dict.
  Convert: `list(d.items())` before building the array.
- Value type variants tested:
  - `MAP(STRING, INT)` → `pa.map_(pa.string(), pa.int64())`
  - `MAP(STRING, DOUBLE)` → `pa.map_(pa.string(), pa.float64())`
  - `MAP(STRING, STRING)` → `pa.map_(pa.string(), pa.string())`
  - `MAP(STRING, BOOLEAN)` → `pa.map_(pa.string(), pa.bool_())`
  - `MAP(STRING, DECIMAL)` → `pa.map_(pa.string(), pa.decimal128(p,s))`
- Mixed-value-type MAPs must be serialized to JSON string (no Arrow equivalent).

### 15. NULL handling
- Every Arrow type supports nulls by default (nullable=True is the default).
- Pass `None` in Python lists; Arrow will encode as null bitmap.
- For ARRAY columns: `None` = null array (whole column null), `[]` = empty array, `[None, 1]` = array with null element.
- For STRUCT columns: `None` = null struct row; `{'f1': None, 'f2': 1}` = struct with null field.
- For MAP columns: `None` = null map row; `[]` = empty map.
- Always test null at: top-level column, inside array element, inside struct field, inside map value.

---

## APPROACH A vs APPROACH B

### Approach A — Arrow-native (zero-copy path)
- Use `snowflake.connector.DictCursor` with `fetch_arrow_batches()` or the `fetchall_arrow()` method.
- Snowflake Python connector (≥2.7.0) supports `cursor.fetch_arrow_batches()` returning `pyarrow.RecordBatch` objects.
- Cast schema via `batch.cast(target_schema)` or `pa.RecordBatch.from_pydict(...)`.
- Advantage: avoids Python object allocation for large result sets; much faster.
- Caveat: Snowflake's inferred Arrow schema may differ from your target schema — always cast explicitly.
- Caveat: VARIANT, OBJECT, ARRAY columns come as strings in the Arrow result — you must parse them.

### Approach B — dict/row path (universal fallback)
- `cursor.fetchall()` → list of tuples or dicts.
- Build Python lists per column, apply converters, then `pa.array(col_list, type=arrow_type)`.
- Slower but gives full Python-level control over every value.
- Better for debugging type issues.

### Approach comparison result (v2 baseline)
- Approach A is ~3–5× faster for >10k rows on numeric columns.
- Approach A requires extra parsing for complex types (VARIANT/OBJECT/ARRAY come as JSON strings).
- Approach B is simpler for struct/map/array with custom converters.
- **Recommendation**: use Approach A for bulk numeric/string columns; fall back to Approach B for complex nested types unless you write custom Arrow casters.

---

## SCHEMA DEFINITION RULES

1. **Always define `pa.schema()` explicitly** — never infer from data.
2. Field nullability: all fields default to nullable; set `nullable=False` only when you can guarantee no NULLs.
3. Metadata: attach Snowflake source type as Arrow field metadata for lineage:
   ```python
   pa.field('col', pa.int64(), metadata={'sf_type': 'NUMBER(18,0)'})
   ```
4. Use `pa.schema().with_metadata({'source': 'snowflake', 'generated': '...'})` for table-level metadata.

---

## WRITE / READ CAVEATS

### Writing
- Use `pq.write_table(table, path, compression='snappy')` as default.
- For DECIMAL columns: `use_deprecated_int96_timestamps=False` (default is fine in PyArrow ≥ 4).
- Set `write_statistics=True` for better read performance.

### Reading back
- `pq.read_table(path)` preserves Arrow schema including DECIMAL precision.
- For TIMESTAMP columns: pandas `.dt.to_pydatetime()` or compare as Arrow arrays directly.
- For DECIMAL: read back as `pa.decimal128`; convert to `Decimal` for Python comparison.
- For JSON/VARIANT string columns: always `json.loads()` before comparing.
- For DATE columns: Arrow returns `datetime.date` after `.to_pydict()`; compare directly.

---

## TESTING STRATEGY

- Generate N=2000 rows with explicit Snowflake type casts.
- Include at minimum: 1 null row (all nullable fields = NULL), boundary rows (min/max values).
- Pytest fixtures: `generate_rows()`, `write_parquet()`, `read_parquet()`, `compare_parity()`.
- Parity check: compare row-by-row with type-aware equality (Decimal, date, datetime, bytes, list, dict).
- Tolerance for FLOAT: `abs(a - b) < 1e-9`.
- For nested types: recursive equality helper that handles None, list, dict.

---

## VERSION HISTORY
- v1: 15 columns, 15 caveats, basic primitives.
- v2: 33 columns, 39 pytest tests, 100% pass. Added timestamp int-math, Approach A/B, verifier.
- v3: Added strong-typed structs, ARRAY of DATE/TS/DECIMAL/BOOL, ARRAY of STRUCT,
       MAP variants (int/double/string/bool/decimal values), NULL matrix,
       VARIANT→JSON confirmed, full Approach A/B benchmark harness.
