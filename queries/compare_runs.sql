-- Two-run comparison per task. Parameters: a, b (run_ids).
SELECT task_id,
       round(avgIf(passed, run_id = {a:String}) * 100) AS a_pass_pct,
       round(avgIf(passed, run_id = {b:String}) * 100) AS b_pass_pct,
       b_pass_pct - a_pass_pct AS delta_pts,
       round(avgIf(cost_usd, run_id = {a:String}), 5) AS a_cost,
       round(avgIf(cost_usd, run_id = {b:String}), 5) AS b_cost
FROM {db}.sessions FINAL
WHERE run_id IN ({a:String}, {b:String}) AND passed IS NOT NULL
GROUP BY task_id
HAVING countIf(run_id = {a:String}) > 0 AND countIf(run_id = {b:String}) > 0
ORDER BY delta_pts, task_id
