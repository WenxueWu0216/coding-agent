-- Tool latency by tool, from tool_result events.
SELECT tool_name, count() AS calls, countIf(status = 'error') AS errors,
       round(quantile(0.5)(latency_ms)) AS p50_ms, round(quantile(0.95)(latency_ms)) AS p95_ms,
       max(latency_ms) AS max_ms
FROM {db}.events
WHERE type = 'tool_result'
GROUP BY tool_name
ORDER BY p95_ms DESC
