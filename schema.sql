-- Trace analytics schema. {db} is replaced by loader.py (agent_analytics for real runs,
-- agent_analytics_bench for synthetic benchmark data, so the two never mix).

CREATE DATABASE IF NOT EXISTS {db};

-- One row per trace event. ReplacingMergeTree is a backstop for idempotency: the sorting key
-- (session_id, ts, event_id) is fixed for a given event, so a duplicate insert collapses on
-- merge. loader.py also skips event_ids that are already stored, so duplicates normally
-- never reach the table and the materialized view below never sees an event twice.
CREATE TABLE IF NOT EXISTS {db}.events
(
    run_id             String,
    session_id         String,
    event_id           String,
    parent_event_id    Nullable(String),
    ts                 DateTime64(3, 'UTC'),
    type               LowCardinality(String),
    model              LowCardinality(String),
    input_tokens       UInt32,
    output_tokens      UInt32,
    latency_ms         UInt32,
    tool_name          LowCardinality(String),
    tool_args_hash     String,
    args_size_bytes    UInt32,
    status             LowCardinality(String),
    error              LowCardinality(String),
    cost_usd           Float64,
    step               UInt16,
    task_id            LowCardinality(String),   -- copied from run_start onto every event of the session
    suite              LowCardinality(String),
    passed             Nullable(UInt8),          -- run_end only
    failure_type       LowCardinality(String),   -- run_end only
    failure_root_cause LowCardinality(String),   -- run_end only
    attrs              String,                   -- remaining event fields as JSON
    source_file        String,
    ingested_at        DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(ts)
ORDER BY (session_id, ts, event_id);

-- One row per finished session, built from its run_end event (which carries the totals).
-- Sessions that never wrote run_end (crashed, or still running) appear only in events.
CREATE TABLE IF NOT EXISTS {db}.sessions
(
    session_id         String,
    run_id             String,
    task_id            LowCardinality(String),
    suite              LowCardinality(String),
    model              LowCardinality(String),
    started_at         DateTime64(3, 'UTC'),
    ended_at           DateTime64(3, 'UTC'),
    duration_ms        UInt32,
    input_tokens       UInt64,
    output_tokens      UInt64,
    cost_usd           Float64,
    tool_calls         UInt32,
    edits              UInt32,
    steps              UInt32,
    passed             Nullable(UInt8),
    status             LowCardinality(String),   -- passed | failed | completed (no verdict, e.g. CLI)
    failure_type       LowCardinality(String),
    failure_root_cause LowCardinality(String),
    ingested_at        DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY session_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.sessions_mv TO {db}.sessions AS
SELECT
    session_id,
    run_id,
    task_id,
    suite,
    model,
    ifNull(parseDateTime64BestEffortOrNull(JSONExtractString(attrs, 'started_at'), 3, 'UTC'),
           ts - toIntervalMillisecond(latency_ms)) AS started_at,
    ts AS ended_at,
    latency_ms AS duration_ms,
    input_tokens,
    output_tokens,
    cost_usd,
    JSONExtractUInt(attrs, 'tool_calls') AS tool_calls,
    JSONExtractUInt(attrs, 'edits') AS edits,
    JSONExtractUInt(attrs, 'steps') AS steps,
    passed,
    multiIf(passed = 1, 'passed', passed = 0, 'failed', 'completed') AS status,
    failure_type,
    failure_root_cause,
    ingested_at
FROM {db}.events
WHERE type = 'run_end';

-- Rows the loader refused, so bad data is visible instead of silently dropped.
CREATE TABLE IF NOT EXISTS {db}.rejected_rows
(
    source_file String,
    line        UInt32,
    reason      String,
    raw         String,
    ingested_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(ingested_at)   -- re-loading a file replaces its old rejects
ORDER BY (source_file, line);
