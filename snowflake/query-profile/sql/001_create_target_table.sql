CREATE SCHEMA IF NOT EXISTS OBSERVABILITY;  -- match this in the config.toml

USE SCHEMA OBSERVABILITY;

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
  operator_attrs$functions ARRAY(VARCHAR) COMMENT '[WindowFunction,Aggregate]',
  -- (remaining sparsely-populated items)
  OPERATOR_STATISTICS VARIANT,
  OPERATOR_ATTRIBUTES VARIANT
) COMMENT = 'GET_QUERY_OPERATOR_STATS() for expensive queries';

CREATE STAGE IF NOT EXISTS QUERY_OPERATOR_STATS_STAGE
  FILE_FORMAT = (TYPE = 'PARQUET')
  COMMENT = 'Internal Stage for Python-generated shredded GET_QUERY_OPERATOR_STATS';



-- Grant the specific database role to the user
GRANT USAGE on schema OBSERVABILITY to USER <XYZ>;  -- to ROLE <XYZ>
GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE QUERY_OPERATOR_STATS_HISTORY TO USER <XYZ>;
GRANT READ,WRITE ON STAGE QUERY_OPERATOR_STATS_STAGE TO USER <XYZ>;

