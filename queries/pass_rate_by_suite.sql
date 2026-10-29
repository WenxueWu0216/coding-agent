-- Pass rate per suite (eval sessions only; CLI sessions have no verdict).
SELECT suite, model, count() AS sessions, sum(passed) AS n_passed, round(avg(passed) * 100, 1) AS pass_pct
FROM {db}.sessions FINAL
WHERE passed IS NOT NULL
GROUP BY suite, model
ORDER BY suite, model
