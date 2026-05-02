-- =============================================================================
-- SF → Parquet Harness v3: Data Generation SQL
-- All columns use EXPLICIT type casts (field_name::TYPE)
-- Covers: primitives, binary, variant, typed arrays, named structs,
--         array of struct, map/dict variants, NULLs
-- =============================================================================

-- Run this in Snowflake to generate the test dataset.
-- Adjust :row_count binding or replace with a literal (e.g. 8192).

WITH seq AS (
    -- Generate :row_count rows (replace 8192 with your desired count)
    SELECT ROW_NUMBER() OVER (ORDER BY SEQ4()) AS rn
    FROM TABLE(GENERATOR(ROWCOUNT => 8192))
),
base AS (
    SELECT
        rn,
        -- Inject nulls every 50 rows (for NULL matrix testing)
        (rn % 50 = 0) AS is_null_row
    FROM seq
)
SELECT
    -- -------------------------------------------------------------------------
    -- ROW METADATA
    -- -------------------------------------------------------------------------
    rn::INTEGER                                             AS row_id,
    is_null_row::BOOLEAN                                    AS is_null_row,

    -- -------------------------------------------------------------------------
    -- PRIMITIVE TYPES
    -- -------------------------------------------------------------------------
    -- BOOLEAN
    IFF(is_null_row, NULL, (rn % 8 = 0))::BOOLEAN          AS col_bool,

    -- INTEGER family
    IFF(is_null_row, NULL, rn::SMALLINT)                   AS col_int16,
    IFF(is_null_row, NULL, rn::INTEGER)                    AS col_int32,
    IFF(is_null_row, NULL, rn::BIGINT)                     AS col_int64,

    -- DECIMAL / NUMBER family
    IFF(is_null_row, NULL, (rn * 1.5)::NUMBER(10, 4))      AS col_decimal_10_4,
    IFF(is_null_row, NULL, (rn * 123456.789012345678)::NUMBER(38, 12))
                                                            AS col_decimal_38_12,

    -- FLOAT / DOUBLE
    IFF(is_null_row, NULL, (rn * 3.14159265358979)::DOUBLE) AS col_double,
    IFF(is_null_row, NULL, (1.0 / rn)::FLOAT)              AS col_float,

    -- STRING / TEXT
    IFF(is_null_row, NULL, ('row_' || rn::VARCHAR))::VARCHAR(64)
                                                            AS col_varchar,
    IFF(is_null_row, NULL,
        RPAD('text_', LEAST(rn, 500), 'abcdefghij'))::TEXT  AS col_text,

    -- -------------------------------------------------------------------------
    -- DATE / TIME / TIMESTAMP
    -- -------------------------------------------------------------------------
    IFF(is_null_row, NULL,
        DATEADD(DAY, rn, '2000-01-01'::DATE))::DATE         AS col_date,

    IFF(is_null_row, NULL,
        TO_TIME(LPAD((rn % 86400)::VARCHAR, 5, '0') || '512'))::TIME(6)
                                                            AS col_time_us,

    IFF(is_null_row, NULL,
        DATEADD(MILLISECOND, rn * 1000,
            '2020-01-01 00:00:00.000'::TIMESTAMP_NTZ(3)))::TIMESTAMP_NTZ(3)
                                                            AS col_ts_ms,

    IFF(is_null_row, NULL,
        DATEADD(MICROSECOND, rn * 6000,
            '2020-01-01 00:00:00.000000'::TIMESTAMP_NTZ(6)))::TIMESTAMP_NTZ(6)
                                                            AS col_ts_us,

    IFF(is_null_row, NULL,
        DATEADD(NANOSECOND, rn * 9000,
            '2020-01-01 00:00:00.000000000'::TIMESTAMP_NTZ(9)))::TIMESTAMP_NTZ(9)
                                                            AS col_ts_ns,

    IFF(is_null_row, NULL,
        DATEADD(SECOND, rn,
            '2020-01-01 00:00:00 +00:00'::TIMESTAMP_LTZ(6)))::TIMESTAMP_LTZ(6)
                                                            AS col_ts_ltz,

    -- -------------------------------------------------------------------------
    -- BINARY
    -- -------------------------------------------------------------------------
    IFF(is_null_row, NULL,
        HEX_DECODE_BINARY(LPAD(HEX_ENCODE(rn), 8, '0')))::BINARY(4)
                                                            AS col_binary,

    -- -------------------------------------------------------------------------
    -- VARIANT  (JSON serialized → Parquet STRING)
    -- -------------------------------------------------------------------------
    IFF(is_null_row, NULL,
        PARSE_JSON('{"id": ' || rn::VARCHAR ||
                   ', "score": ' || (rn * 0.5)::VARCHAR ||
                   ', "tags": ["a","b"]' ||
                   ', "nested": {"x": ' || rn::VARCHAR || '}}'))::VARIANT
                                                            AS col_variant,

    -- VARIANT: array of mixed
    IFF(is_null_row, NULL,
        PARSE_JSON('[' || rn::VARCHAR || ', "str_' || rn::VARCHAR ||
                   '", true, null]'))::VARIANT              AS col_variant_array,

    -- -------------------------------------------------------------------------
    -- STRONGLY TYPED NAMED STRUCT
    -- (OBJECT_CONSTRUCT produces a typed OBJECT; use CAST for sub-fields)
    -- -------------------------------------------------------------------------
    IFF(is_null_row, NULL,
        OBJECT_CONSTRUCT(
            'user_id',      rn::INTEGER,
            'username',     ('user_' || rn::VARCHAR)::VARCHAR,
            'score',        (rn * 1.5)::NUMBER(10, 4),
            'is_active',    (rn % 3 != 0)::BOOLEAN,
            'signup_date',  DATEADD(DAY, rn, '2010-01-01'::DATE)::DATE::VARCHAR
        ))::VARIANT                                         AS col_struct_user,

    IFF(is_null_row, NULL,
        OBJECT_CONSTRUCT(
            'lat',          (rn % 90 - 45)::NUMBER(8, 6),
            'lon',          (rn % 180 - 90)::NUMBER(9, 6),
            'altitude_m',   (rn * 10)::INTEGER,
            'label',        ('loc_' || rn::VARCHAR)::VARCHAR
        ))::VARIANT                                         AS col_struct_geo,

    -- -------------------------------------------------------------------------
    -- ARRAY OF TYPED SCALARS
    -- -------------------------------------------------------------------------
    -- ARRAY(INTEGER)
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            rn::INTEGER, (rn+1)::INTEGER, (rn+2)::INTEGER, (rn+3)::INTEGER
        ))::VARIANT                                         AS col_arr_int,

    -- ARRAY(DOUBLE)
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            (rn * 1.1)::DOUBLE, (rn * 2.2)::DOUBLE, (rn * 3.3)::DOUBLE
        ))::VARIANT                                         AS col_arr_double,

    -- ARRAY(DECIMAL(10,4))
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            (rn * 0.0001)::NUMBER(10,4),
            (rn * 0.0002)::NUMBER(10,4),
            (rn * 0.0003)::NUMBER(10,4)
        ))::VARIANT                                         AS col_arr_decimal,

    -- ARRAY(DATE) — stored as ISO string inside VARIANT
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            DATEADD(DAY, rn,   '2023-01-01'::DATE)::DATE::VARCHAR,
            DATEADD(DAY, rn+1, '2023-01-01'::DATE)::DATE::VARCHAR,
            DATEADD(DAY, rn+2, '2023-01-01'::DATE)::DATE::VARCHAR
        ))::VARIANT                                         AS col_arr_date,

    -- ARRAY(TIMESTAMP_NTZ(6)) — stored as ISO string inside VARIANT
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            DATEADD(MICROSECOND, rn*1000, '2022-01-01 00:00:00'::TIMESTAMP_NTZ(6))::VARCHAR,
            DATEADD(MICROSECOND, rn*2000, '2022-01-01 00:00:00'::TIMESTAMP_NTZ(6))::VARCHAR
        ))::VARIANT                                         AS col_arr_ts,

    -- ARRAY(BOOLEAN)
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            (rn % 2 = 0)::BOOLEAN,
            (rn % 3 = 0)::BOOLEAN,
            (rn % 5 = 0)::BOOLEAN
        ))::VARIANT                                         AS col_arr_bool,

    -- ARRAY(VARCHAR)
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            ('item_' || rn::VARCHAR)::VARCHAR,
            ('item_' || (rn+100)::VARCHAR)::VARCHAR
        ))::VARIANT                                         AS col_arr_string,

    -- -------------------------------------------------------------------------
    -- ARRAY OF STRUCT
    -- -------------------------------------------------------------------------
    IFF(is_null_row, NULL,
        ARRAY_CONSTRUCT(
            OBJECT_CONSTRUCT('event_id', rn::INTEGER,
                             'event_ts', DATEADD(SECOND, rn, '2024-01-01'::TIMESTAMP_NTZ(6))::VARCHAR,
                             'value',    (rn * 0.01)::NUMBER(12,4)),
            OBJECT_CONSTRUCT('event_id', (rn+1000)::INTEGER,
                             'event_ts', DATEADD(SECOND, rn+1, '2024-01-01'::TIMESTAMP_NTZ(6))::VARCHAR,
                             'value',    ((rn+1) * 0.01)::NUMBER(12,4))
        ))::VARIANT                                         AS col_arr_struct,

    -- -------------------------------------------------------------------------
    -- MAP / DICT with different value types
    -- -------------------------------------------------------------------------
    -- MAP(STRING → INTEGER)
    IFF(is_null_row, NULL,
        OBJECT_CONSTRUCT(
            'count',  rn::INTEGER,
            'total',  (rn * 10)::INTEGER,
            'max',    (rn + 100)::INTEGER
        ))::VARIANT                                         AS col_map_str_int,

    -- MAP(STRING → DOUBLE)
    IFF(is_null_row, NULL,
        OBJECT_CONSTRUCT(
            'p50',  (rn * 0.5)::DOUBLE,
            'p95',  (rn * 0.95)::DOUBLE,
            'p99',  (rn * 0.99)::DOUBLE
        ))::VARIANT                                         AS col_map_str_double,

    -- MAP(STRING → STRING)
    IFF(is_null_row, NULL,
        OBJECT_CONSTRUCT(
            'env',    'prod'::VARCHAR,
            'region', ('us-west-' || (rn % 3 + 1)::VARCHAR)::VARCHAR,
            'tier',   IFF(rn % 2 = 0, 'premium', 'standard')::VARCHAR
        ))::VARIANT                                         AS col_map_str_string,

    -- MAP(STRING → BOOLEAN)
    IFF(is_null_row, NULL,
        OBJECT_CONSTRUCT(
            'enabled',    (rn % 2 = 0)::BOOLEAN,
            'archived',   (rn % 7 = 0)::BOOLEAN,
            'verified',   (rn % 3 = 0)::BOOLEAN
        ))::VARIANT                                         AS col_map_str_bool,

    -- MAP(STRING → DECIMAL)
    IFF(is_null_row, NULL,
        OBJECT_CONSTRUCT(
            'price',    (rn * 9.99)::NUMBER(12,4),
            'tax',      (rn * 0.08)::NUMBER(12,4),
            'discount', (rn * 0.05)::NUMBER(12,4)
        ))::VARIANT                                         AS col_map_str_decimal,

    -- -------------------------------------------------------------------------
    -- NULL MATRIX — always-null columns (test null handling per type)
    -- -------------------------------------------------------------------------
    NULL::INTEGER                                           AS col_always_null_int,
    NULL::VARCHAR                                           AS col_always_null_str,
    NULL::TIMESTAMP_NTZ(6)                                  AS col_always_null_ts,
    NULL::VARIANT                                           AS col_always_null_variant,
    NULL::BOOLEAN                                           AS col_always_null_bool

FROM base
ORDER BY rn;
