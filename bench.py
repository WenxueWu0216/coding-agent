"""SYNTHETIC load for benchmarking the API. Never used for real results.

  python bench.py seed      write seed traces (see below) to evals/synthetic/seed/
  python bench.py load      load the seed into agent_analytics_bench and replay it to ~5M events
  python bench.py measure   time every API endpoint; durations come from system.query_log
  python bench.py optimize  apply the optimization (see OPTIMIZATION) to the bench database

Everything lives in a separate database (agent_analytics_bench), every run_id starts with
"synthetic-", and every row has source_file = 'synthetic', so synthetic data can't leak into
the real-run database or its query results.

Seeds: by default real traces from evals/runs/*/traces are replayed. With --scripted-seed the
seed is produced by running the real harness with a scripted fake model (reference solutions
plus a few failure patterns) instead, for when no real traces exist.
"""
import argparse
import statistics
import sys
import time
import uuid
from pathlib import Path

import loader

BENCH_DB = "agent_analytics_bench"
SEED_DIR = loader.PROJECT_DIR / "evals" / "synthetic" / "seed"
TARGET_EVENTS = 5_000_000
N_RUNS = 200
REPEATS = 7

# The optimization being measured; schema.sql ships with it. `load` removes it so the
# baseline can be measured, `optimize` puts it back. See README "Performance".
OPTIMIZATION = [
    "ALTER TABLE {db}.events ADD PROJECTION IF NOT EXISTS by_run (SELECT * ORDER BY run_id, session_id, ts, seq)",
    "ALTER TABLE {db}.events MATERIALIZE PROJECTION by_run SETTINGS mutations_sync = 2",
]
UNOPTIMIZE = "ALTER TABLE {db}.events DROP PROJECTION IF EXISTS by_run"


def seed_scripted() -> None:
    import eval as ev
    from test_eval import FakeClient, OracleClient, text, tool

    SEED_DIR.mkdir(parents=True, exist_ok=True)
    tasks = ev.load_tasks()
    failures = {
        "lazy": lambda t: FakeClient([([text("Looks fine.")], "end_turn")]),
        "loop": lambda t: FakeClient([([tool(i, "run_bash", {"command": "ls"})], "tool_use") for i in range(8)]),
        "bad_context": lambda t: FakeClient([
            ([tool(1, "read_file", {"path": "nope.py"})], "tool_use"),
            ([tool(2, "edit_file", {"path": "nope.py", "old_str": "a", "new_str": "b"})], "tool_use"),
            ([text("done")], "end_turn")]),
    }
    for i, t in enumerate(tasks):
        ev.run_task(t, "claude-haiku-5-5", 8, client=OracleClient(t), trace_path=SEED_DIR / f"{t['id']}__ok.jsonl",
                    run_id="synthetic-seed")
        name = list(failures)[i % len(failures)]
        ev.run_task(t, "claude-haiku-5-5", 8, client=failures[name](t),
                    trace_path=SEED_DIR / f"{t['id']}__{name}.jsonl", run_id="synthetic-seed")
    print(f"wrote {2 * len(tasks)} SYNTHETIC seed traces (scripted fake model) to {SEED_DIR}")


def load_and_replay(client, db: str, seed_paths: list[Path]) -> None:
    client.command(f"DROP DATABASE IF EXISTS {db}")
    loader.ensure_schema(client, db)
    client.command(UNOPTIMIZE.format(db=db))
    staging = f"{db}.seed_events"
    client.command(f"CREATE TABLE {staging} AS {db}.events ENGINE = MergeTree ORDER BY (session_id, ts, seq)")
    files = loader.trace_files(seed_paths)
    batch = loader.Batch()
    for f in files:
        loader.read_file(f, batch)
    client.insert(staging, batch.rows, column_names=loader.COLUMNS)
    seed_events = len(batch.rows)
    copies = -(-TARGET_EVENTS // seed_events)
    print(f"seed: {len(files)} traces, {seed_events} events; replaying {copies} copies")

    # Each copy gets fresh, uniformly random session/event ids (like real uuid4s), a run among
    # N_RUNS synthetic runs, a model, and a start time spread over the last ~60 days.
    t0 = time.time()
    step = 500
    for lo in range(0, copies, step):
        hi = min(copies, lo + step)
        client.command(f"""
            INSERT INTO {db}.events
            SELECT
                concat('synthetic-run-', toString(k % {N_RUNS})) AS run_id,
                lower(hex(sipHash128(k, session_id))) AS session_id,
                lower(hex(sipHash128(k, event_id))) AS event_id,
                if(parent_event_id IS NULL, NULL, lower(hex(sipHash128(k, assumeNotNull(parent_event_id))))),
                ts - toIntervalSecond(intDiv(k * 5184000, {copies})) AS ts,
                seq, type,
                ['claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5'][1 + (k % 3)] AS model,
                input_tokens, output_tokens, latency_ms, tool_name, tool_args_hash, args_size_bytes, status,
                error, cost_usd, step, task_id, suite, passed, failure_type, failure_root_cause,
                -- drop the seed's started_at so the sessions view derives it from the shifted ts
                replaceRegexpOne(attrs, ', "started_at": "[^"]*"', '') AS attrs,
                'synthetic' AS source_file, now64(3) AS ingested_at
            FROM {staging}
            ARRAY JOIN range({lo}, {hi}) AS k
        """, settings={"max_insert_threads": 4})
    client.command(f"DROP TABLE {staging}")
    client.command(f"OPTIMIZE TABLE {db}.events FINAL")
    client.command(f"OPTIMIZE TABLE {db}.sessions FINAL")
    n = client.query(f"SELECT count() FROM {db}.events").result_rows[0][0]
    s = client.query(f"SELECT count() FROM {db}.sessions").result_rows[0][0]
    print(f"loaded {n:,} SYNTHETIC events / {s:,} sessions into {db} in {time.time() - t0:.0f}s")


def scenarios(client, db: str) -> dict[str, tuple[str, dict]]:
    """API calls to time, with parameters picked from the data."""
    sid = client.query(f"SELECT session_id FROM {db}.sessions WHERE task_id = '14_lru' LIMIT 1").result_rows[0][0]
    day = client.query(f"SELECT toDate(max(started_at)) - 10 FROM {db}.sessions").result_rows[0][0]
    return {
        "sessions_page1": ("/sessions", {"limit": 50}),
        "sessions_filtered": ("/sessions", {"limit": 50, "task_id": "14_lru", "status": "failed",
                                            "model": "claude-sonnet-5-5"}),
        "sessions_date_range": ("/sessions", {"limit": 50, "since": f"{day}T00:00:00Z", "until": f"{day}T23:59:59Z"}),
        "session_detail": (f"/sessions/{sid}", {}),
        "runs_compare": ("/runs/compare", {"a": "synthetic-run-1", "b": "synthetic-run-2"}),
        "export_session_csv": ("/export", {"session_id": sid, "format": "csv"}),
        "export_run_json": ("/export", {"run_id": "synthetic-run-7", "format": "json"}),
    }


def measure(url: str, db: str, label: str) -> dict[str, dict]:
    from fastapi.testclient import TestClient

    from api import create_app

    base = loader.connect(url)
    tag = f"bench-{label}-{uuid.uuid4().hex[:6]}"
    results = {}
    for name, (path, params) in scenarios(base, db).items():
        client = loader.connect(url, autogenerate_session_id=False,
                                settings={"log_comment": f"{tag}:{name}", "use_query_cache": 0})
        http = TestClient(create_app(client, db))
        wall = []
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            r = http.get(path, params=params)
            wall.append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 200, (name, r.status_code, r.text[:200])
        results[name] = {"wall_ms": statistics.median(wall), "bytes": len(r.content),
                         "items": len(r.json().get("items", [])) if path == "/sessions" else None}
    base.command("SYSTEM FLUSH LOGS")
    rows = base.query("""
        SELECT log_comment, count(), sum(query_duration_ms), sum(read_rows), sum(read_bytes)
        FROM system.query_log
        WHERE type = 'QueryFinish' AND startsWith(log_comment, {tag:String})
        GROUP BY log_comment""", parameters={"tag": tag}).result_rows
    for comment, n, dur, read_rows, read_bytes in rows:
        name = comment.split(":", 1)[1]
        results[name].update(queries=n, server_ms=dur / REPEATS, read_rows=read_rows // REPEATS,
                             read_mb=read_bytes / REPEATS / 1e6)
    print(f"\n{label}: {REPEATS} requests each; server_ms = mean ClickHouse query_duration_ms per request, "
          "wall_ms = median end-to-end time")
    print(f"{'endpoint':<22}{'server_ms':>10}{'wall_ms':>10}{'rows read':>14}{'MB read':>10}{'KB out':>9}")
    for name, r in sorted(results.items(), key=lambda kv: -kv[1].get("server_ms", 0)):
        print(f"{name:<22}{r.get('server_ms', 0):>10.1f}{r['wall_ms']:>10.1f}{r.get('read_rows', 0):>14,}"
              f"{r.get('read_mb', 0):>10.1f}{r['bytes'] / 1000:>9.0f}"
              + (f"  ({r['items']} sessions)" if r["items"] is not None else ""))
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["seed", "load", "measure", "optimize"])
    ap.add_argument("--url", default=loader.DEFAULT_URL)
    ap.add_argument("--db", default=BENCH_DB)
    ap.add_argument("--scripted-seed", action="store_true", help="seed from the scripted fake model")
    ap.add_argument("--label", default="run")
    args = ap.parse_args()
    if args.db == loader.DEFAULT_DB:
        sys.exit("refusing to put synthetic data in the real-run database")

    if args.command == "seed":
        if not args.scripted_seed:
            sys.exit("real traces are replayed directly from evals/runs; use --scripted-seed to generate a seed")
        seed_scripted()
        return
    client = loader.connect(args.url, send_receive_timeout=600)
    if args.command == "load":
        paths = [SEED_DIR] if args.scripted_seed else [loader.PROJECT_DIR / "evals" / "runs"]
        load_and_replay(client, args.db, paths)
    elif args.command == "optimize":
        for stmt in OPTIMIZATION:
            client.command(stmt.format(db=args.db))
        print("applied:\n  " + "\n  ".join(s.format(db=args.db) for s in OPTIMIZATION))
    else:
        measure(args.url, args.db, args.label)


if __name__ == "__main__":
    main()
