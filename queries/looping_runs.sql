-- Sessions that repeated the same tool call (same tool and identical args) more than 3 times.
SELECT e.session_id AS session_id, any(s.task_id) AS task, any(s.status) AS session_status,
       e.tool_name AS tool, e.tool_args_hash AS args_hash, count() AS repeats
FROM {db}.events AS e
LEFT JOIN (SELECT session_id, task_id, status FROM {db}.sessions FINAL) AS s ON s.session_id = e.session_id
WHERE e.type = 'tool_call'
GROUP BY session_id, tool, args_hash
HAVING repeats > 3
ORDER BY repeats DESC
LIMIT 50
