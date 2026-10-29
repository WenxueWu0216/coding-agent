-- Tokens and cost per eval run (run_id = one eval.py invocation).
SELECT run_id, any(model) AS model, count() AS sessions,
       sum(input_tokens) AS total_input, sum(output_tokens) AS total_output,
       round(sum(cost_usd), 4) AS total_cost_usd, round(avg(cost_usd), 5) AS cost_per_session,
       round(avg(input_tokens + output_tokens)) AS tokens_per_session,
       round(quantile(0.5)(duration_ms) / 1000, 1) AS p50_duration_s
FROM {db}.sessions FINAL
GROUP BY run_id
ORDER BY run_id
