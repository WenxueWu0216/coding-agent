-- Most common failure root causes among failed eval sessions.
SELECT failure_root_cause, count() AS sessions,
       round(count() * 100 / sum(count()) OVER (), 1) AS pct,
       groupUniqArray(failure_type) AS failure_types,
       arraySlice(groupUniqArray(task_id), 1, 5) AS example_tasks
FROM {db}.sessions FINAL
WHERE status = 'failed'
GROUP BY failure_root_cause
ORDER BY sessions DESC
