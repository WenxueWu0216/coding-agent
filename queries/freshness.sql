-- Ingestion freshness: how long after an event happened it became queryable.
SELECT run_id, count() AS events,
       round(quantile(0.5)(dateDiff('millisecond', ts, ingested_at)) / 1000, 1) AS p50_lag_s,
       round(quantile(0.95)(dateDiff('millisecond', ts, ingested_at)) / 1000, 1) AS p95_lag_s,
       round(max(dateDiff('millisecond', ts, ingested_at)) / 1000, 1) AS max_lag_s,
       max(ingested_at) AS last_ingested
FROM {db}.events
GROUP BY run_id
ORDER BY last_ingested DESC
