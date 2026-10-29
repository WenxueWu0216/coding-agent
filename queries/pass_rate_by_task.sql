-- Pass rate per task, worst first.
SELECT suite, task_id, count() AS sessions, sum(passed) AS n_passed, round(avg(passed) * 100, 1) AS pass_pct
FROM {db}.sessions FINAL
WHERE passed IS NOT NULL
GROUP BY suite, task_id
ORDER BY pass_pct, suite, task_id
