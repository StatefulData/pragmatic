USE SCHEMA XYZ;  -- please change to the effective schema in production

CREATE OR REPLACE TRANSIENT TABLE QUERY_OPERATOR_STATS_HISTORY CLUSTER BY (START_TIME::DATE) (
  QUERY_ID STRING NOT NULL,
  QUERY_TAG STRING,
  START_TIME TIMESTAMP_NTZ(3) NOT NULL,
  END_TIME TIMESTAMP_NTZ(3) NOT NULL,
  EXECUTION_TIME INT NOT NULL,
  STEP_ID INT NOT NULL,
  OPERATOR_ID INT NOT NULL,
  PARENT_OPERATORS ARRAY(INT),
  OPERATOR_TYPE STRING NOT NULL,
  -- (shredded) critical metrics for performance analytics
  execution_time$overall_percentage FLOAT,
  execution_time$initialization FLOAT,
  execution_time$processing FLOAT,
  execution_time$synchronization FLOAT,
  execution_time$local_disk_io FLOAT,
  execution_time$remote_disk_io FLOAT,
  execution_time$network_communication FLOAT,
  operator_stats$input_rows INT,
  operator_stats$output_rows INT,
  operator_stats$network_bytes INT,
  operator_stats$bytes_spilled_local_storage INT,
  operator_stats$bytes_spilled_remote_storage INT,
  operator_stats$bytes_scanned INT COMMENT '[io]',
  operator_stats$bytes_written INT COMMENT '[io]',
  operator_stats$bytes_written_to_result INT COMMENT '[io]',
  operator_stats$partitions_scanned INT COMMENT '[pruning]',
  operator_stats$partitions_total INT COMMENT '[pruning]]',

  operator_attrs$grouping_keys ARRAY(VARCHAR) COMMENT '[Aggregate,Pivot]',
  operator_attrs$join_type VARCHAR COMMENT '[Join,CartesianJoin]',
  operator_attrs$equality_join_condition VARCHAR COMMENT '[Join,CartesianJoin]',
  operator_attrs$additional_join_condition VARCHAR COMMENT '[Join,CartesianJoin]',
  operator_attrs$table_name VARCHAR COMMENT '[Update,Merge,Delete,TableScan]',
  operator_attrs$filter_condition VARCHAR COMMENT '[Filter]',
  operator_attrs$join_id INT COMMENT '[JoinFilter]',
  operator_attrs$columns ARRAY(VARCHAR) COMMENT '[TableScan]',
  operator_attrs$expressions ARRAY(VARCHAR) COMMENT '[TableScan]',
  operator_attrs$functions VARCHAR COMMENT '[WindowFunction,Aggregate]',
  -- (remaining sparsely-populated items)
  OPERATOR_STATISTICS VARIANT,
  OPERATOR_ATTRIBUTES VARIANT
) COMMENT = 'GET_QUERY_OPERATOR_STATS() for expensive queries';

CREATE OR REPLACE PROCEDURE PUBLIC.GET_EXPENSIVE_QUERY_OPERATOR_STATS(
  lookback_hours INTEGER, min_execute_minutes INTEGER)
RETURNS INTEGER NOT NULL
LANGUAGE SQL
AS
$$
DECLARE
  cur1 CURSOR FOR
    SELECT QUERY_ID, QUERY_TAG, START_TIME, END_TIME, EXECUTION_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    WHERE CLUSTER_NUMBER > 0
      AND END_TIME IS NOT NULL
      AND EXECUTION_TIME >= :min_execute_minutes * 60 * 1000
      AND START_TIME >= TIMESTAMPADD(HOUR, - :lookback_hours, DATE_TRUNC(HOUR, CURRENT_TIMESTAMP))
      AND QUERY_ID NOT IN (
        SELECT QUERY_ID FROM XYZ.QUERY_OPERATOR_STATS_HISTORY
        WHERE START_TIME >= TIMESTAMPADD(HOUR, - :lookback_hours, DATE_TRUNC(HOUR, CURRENT_TIMESTAMP))
      );
  record_count INTEGER DEFAULT 0;
  query_id STRING;
  query_tag STRING;
  start_time TIMESTAMP_NTZ;
  end_time TIMESTAMP_NTZ;
  execution_time INT;
BEGIN
  IF (lookback_hours < 1) THEN
    lookback_hours := 4;
  END IF;
  IF (min_execute_minutes < 1) THEN
    min_execute_minutes := 10;
  END IF;
  OPEN cur1;
  FOR record IN cur1 DO
    FETCH cur1 INTO query_id, query_tag, start_time, end_time, execution_time;

    INSERT INTO XYZ.QUERY_OPERATOR_STATS_HISTORY  -- single row insert is super inefficient in Snowflake, this is just a demo
      SELECT
        :query_id, :query_tag, :start_time, :end_time, :execution_time,
        STEP_ID, OPERATOR_ID, PARENT_OPERATORS, OPERATOR_TYPE,

        EXECUTION_TIME_BREAKDOWN:overall_percentage::float execution_time$overall_percentage,
        EXECUTION_TIME_BREAKDOWN:initialization::float execution_time$initialization,
        EXECUTION_TIME_BREAKDOWN:processing::float execution_time$processing,
        EXECUTION_TIME_BREAKDOWN:synchronization::float execution_time$synchronization,
        EXECUTION_TIME_BREAKDOWN:local_disk_io::float execution_time$local_disk_io,
        EXECUTION_TIME_BREAKDOWN:remote_disk_io::float execution_time$remote_disk_io,
        EXECUTION_TIME_BREAKDOWN:network_communication::float execution_time$network_communication,

        OPERATOR_STATISTICS:input_rows::int operator_stats$input_rows,
        OPERATOR_STATISTICS:output_rows::int operator_stats$output_rows,
        OPERATOR_STATISTICS:network:network_bytes::int operator_stats$network_bytes,
        OPERATOR_STATISTICS:spilling:bytes_spilled_local_storage::int operator_stats$bytes_spilled_local_storage,
        OPERATOR_STATISTICS:spilling:bytes_spilled_remote_storage::int operator_stats$bytes_spilled_remote_storage,
        OPERATOR_STATISTICS:io:bytes_scanned::int operator_stats$bytes_scanned,
        OPERATOR_STATISTICS:io:bytes_written::int operator_stats$bytes_written,
        OPERATOR_STATISTICS:io:bytes_written_to_result::int operator_stats$bytes_written_to_result,
        OPERATOR_STATISTICS:pruning:partitions_scanned::int operator_stats$partitions_scanned,
        OPERATOR_STATISTICS:pruning:partitions_total::int operator_stats$partitions_total,

        OPERATOR_ATTRIBUTES:grouping_keys::array(varchar) operator_attrs$grouping_keys,
        OPERATOR_ATTRIBUTES:join_type::varchar operator_attrs$join_type,
        OPERATOR_ATTRIBUTES:equality_join_condition::varchar operator_attrs$equality_join_condition,
        OPERATOR_ATTRIBUTES:additional_join_condition::varchar operator_attrs$additional_join_condition,
        coalesce(OPERATOR_ATTRIBUTES:table_name::varchar, OPERATOR_ATTRIBUTES:table_names[0]::varchar) operator_attrs$table_name,
        OPERATOR_ATTRIBUTES:filter_condition::varchar operator_attrs$filter_condition,
        OPERATOR_ATTRIBUTES:join_id::int operator_attrs$join_id,
        OPERATOR_ATTRIBUTES:columns::array(varchar) operator_attrs$columns,
        OPERATOR_ATTRIBUTES:expressions::array(varchar) operator_attrs$expressions,
        OPERATOR_ATTRIBUTES:functions::array(varchar) operator_attrs$functions,

        OBJECT_DELETE(OPERATOR_STATISTICS, 'input_rows','output_rows','network','io','spilling','pruning') as OPERATOR_STATISTICS,
        OBJECT_DELETE(OPERATOR_ATTRIBUTES, 'grouping_keys','join_type','equality_join_condition','additional_join_condition','table_name','filter_condition','join_id','columns','expressions','functions') as OPERATOR_ATTRIBUTES
      FROM table( GET_QUERY_OPERATOR_STATS(:query_id) );

    record_count := record_count + 1;
  END FOR;

  RETURN record_count;
END;
$$
;

CALL PUBLIC.GET_EXPENSIVE_QUERY_OPERATOR_STATS(4, 10);

select get_ddl('procedure','PUBLIC.GET_EXPENSIVE_QUERY_OPERATOR_STATS(INT,INT)');
