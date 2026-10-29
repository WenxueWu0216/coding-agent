"""Load JSONL agent traces into ClickHouse.

  python loader.py                          load evals/runs/*/traces and traces/ once
  python loader.py PATH [PATH ...]          load specific files or directories
  python loader.py --watch                  keep running; load each trace once its run ends

Loading is idempotent: event_ids already stored are skipped, so re-loading a file (or a
file that grew since the last pass) inserts only the new events. Invalid rows are
reported with file:line and a reason, stored in rejected_rows, and the rest of the batch
is still loaded.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from tracing import ENVELOPE, EVENT_TYPES, LEGACY_TYPES

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PATHS = [PROJECT_DIR / "evals" / "runs", PROJECT_DIR / "traces"]
SCHEMA = PROJECT_DIR / "schema.sql"
DEFAULT_DB = os.environ.get("CLICKHOUSE_DB", "agent_analytics")
DEFAULT_URL = os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")

COLUMNS = ["run_id", "session_id", "event_id", "parent_event_id", "ts", "type", "model", "input_tokens",
           "output_tokens", "latency_ms", "tool_name", "tool_args_hash", "args_size_bytes", "status", "error",
           "cost_usd", "step", "task_id", "suite", "passed", "failure_type", "failure_root_cause", "attrs",
           "source_file", "ingested_at"]
REQUIRED_STR = ("run_id", "session_id", "event_id", "type")
NON_NEGATIVE_INT = ("input_tokens", "output_tokens", "latency_ms", "args_size_bytes")
UINT32_MAX = 2**32 - 1


class BadRow(ValueError):
    pass


@dataclass
class Reject:
    source_file: str
    line: int
    reason: str
    raw: str

    def __str__(self) -> str:
        return f"{self.source_file}:{self.line}: {self.reason}"


@dataclass
class Batch:
    rows: list[list[Any]] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)


def parse_ts(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise BadRow("ts must be an ISO-8601 string")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BadRow(f"ts is not ISO-8601: {value[:40]!r}") from None
    if ts.tzinfo is None:
        raise BadRow("ts has no timezone; expected UTC")
    return ts.astimezone(timezone.utc)


def validate(e: Any) -> dict:
    """Return the event with its type normalized, or raise BadRow with the reason."""
    if not isinstance(e, dict):
        raise BadRow("line is not a JSON object")
    if "run_id" not in e and e.get("type") in (*LEGACY_TYPES, "tool_call", "model_call"):
        raise BadRow("missing run_id: legacy trace written before the analytics schema; re-run to load it")
    for k in REQUIRED_STR:
        if not isinstance(e.get(k), str) or not e[k]:
            raise BadRow(f"{k} must be a non-empty string")
    kind = LEGACY_TYPES.get(e["type"], e["type"])
    if kind not in EVENT_TYPES:
        raise BadRow(f"unknown event type {e['type']!r}")
    for k in NON_NEGATIVE_INT:
        v = e.get(k, 0)
        if isinstance(v, bool) or not isinstance(v, int):
            raise BadRow(f"{k} must be an integer, got {type(v).__name__}")
        if v < 0:
            raise BadRow(f"{k} is negative ({v})")
        if v > UINT32_MAX:
            raise BadRow(f"{k} is out of range ({v})")
    cost = e.get("cost_usd", 0.0)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise BadRow(f"cost_usd must be a non-negative number, got {cost!r}")
    for k in ("parent_event_id", "model", "tool_name", "tool_args_hash", "status", "error"):
        if e.get(k) is not None and not isinstance(e[k], str):
            raise BadRow(f"{k} must be a string or null")
    if e.get("passed") is not None and not isinstance(e["passed"], bool):
        raise BadRow("passed must be a boolean or null")
    parse_ts(e.get("ts"))
    return {**e, "type": kind}


def to_row(e: dict, source: str, task_id: str, suite: str, now: datetime) -> list[Any]:
    extra = {k: v for k, v in e.items() if k not in ENVELOPE and k not in
             ("step", "task_id", "suite", "passed", "failure_type", "failure_root_cause")}
    passed = e.get("passed")
    return [e["run_id"], e["session_id"], e["event_id"], e.get("parent_event_id"), parse_ts(e["ts"]), e["type"],
            e.get("model") or "", e.get("input_tokens", 0), e.get("output_tokens", 0), e.get("latency_ms", 0),
            e.get("tool_name") or "", e.get("tool_args_hash") or "", e.get("args_size_bytes", 0),
            e.get("status") or "", e.get("error") or "", float(e.get("cost_usd", 0.0)),
            int(e.get("step") or 0), e.get("task_id") or task_id, e.get("suite") or suite,
            None if passed is None else int(passed), e.get("failure_type") or "",
            e.get("failure_root_cause") or "", json.dumps(extra, default=str), source, now]


def read_file(path: Path, batch: Batch, now: Optional[datetime] = None) -> None:
    """Parse and validate one trace file, appending good rows and rejects to the batch."""
    now = now or datetime.now(timezone.utc)
    source = str(path)
    events: list[tuple[int, dict]] = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append((n, validate(json.loads(line))))
        except json.JSONDecodeError as ex:
            batch.rejects.append(Reject(source, n, f"invalid JSON: {ex.msg}", line[:2000]))
        except BadRow as ex:
            batch.rejects.append(Reject(source, n, str(ex), line[:2000]))
    start = next((e for _, e in events if e["type"] == "run_start"), {})
    for _, e in events:
        batch.rows.append(to_row(e, source, start.get("task_id", ""), start.get("suite", ""), now))


def trace_files(paths: Iterable[Path]) -> list[Path]:
    out = []
    for p in paths:
        if p.is_file() and p.suffix == ".jsonl":
            out.append(p)
        elif p.is_dir():
            out.extend(f for f in p.rglob("*.jsonl") if f.name != "results.jsonl")
    return sorted(out)


def is_finished(path: Path, idle_s: float) -> bool:
    """A trace is ready once it has run_end, or hasn't changed for idle_s (crashed run)."""
    if time.time() - path.stat().st_mtime > idle_s:
        return True
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 8192))
        tail = f.read()
    return b'"type": "run_end"' in tail or b'"type": "task_end"' in tail


# ---------- ClickHouse ----------

def connect(url: str = DEFAULT_URL, **kw: Any):
    import clickhouse_connect
    from urllib.parse import urlparse

    u = urlparse(url)
    return clickhouse_connect.get_client(host=u.hostname or "localhost", port=u.port or 8123,
                                         username=u.username or "default", password=u.password or "", **kw)


def ensure_schema(client, db: str) -> None:
    sql = SCHEMA.read_text().replace("{db}", db)
    lines = [l for l in sql.splitlines() if not l.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        if stmt.strip():
            client.command(stmt)


def existing_event_ids(client, db: str, session_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(session_ids), 1000):
        chunk = session_ids[i:i + 1000]
        r = client.query(f"SELECT event_id FROM {db}.events WHERE session_id IN %(s)s", parameters={"s": chunk})
        found.update(row[0] for row in r.result_rows)
    return found


def insert(client, db: str, batch: Batch, dedup: bool = True) -> tuple[int, int]:
    """Insert the batch. Returns (inserted, skipped as already present)."""
    rows = batch.rows
    skipped = 0
    if dedup and rows:
        seen = existing_event_ids(client, db, sorted({r[1] for r in rows}))
        unique: dict[str, list[Any]] = {}
        for r in rows:
            if r[2] not in seen:
                unique.setdefault(r[2], r)   # also drops duplicates inside the batch
        skipped = len(rows) - len(unique)
        rows = list(unique.values())
    if rows:
        client.insert(f"{db}.events", rows, column_names=COLUMNS)
    if batch.rejects:
        now = datetime.now(timezone.utc)
        client.insert(f"{db}.rejected_rows", [[r.source_file, r.line, r.reason, r.raw, now] for r in batch.rejects],
                      column_names=["source_file", "line", "reason", "raw", "ingested_at"])
    return len(rows), skipped


def load(client, db: str, files: list[Path], batch_size: int = 50_000, log=print) -> dict[str, int]:
    totals = {"files": 0, "inserted": 0, "skipped": 0, "rejected": 0}
    batch = Batch()

    def flush() -> None:
        nonlocal batch
        inserted, skipped = insert(client, db, batch)
        for r in batch.rejects:
            log(f"rejected {r}")
        totals["inserted"] += inserted
        totals["skipped"] += skipped
        totals["rejected"] += len(batch.rejects)
        batch = Batch()

    for f in files:
        read_file(f, batch)
        totals["files"] += 1
        if len(batch.rows) >= batch_size:
            flush()
    flush()
    return totals


def watch(client, db: str, paths: list[Path], interval: float, idle_s: float) -> None:
    """Poll for finished traces and load them. Re-loads a file when it changes; dedup keeps that safe."""
    seen: dict[Path, tuple[int, float]] = {}
    print(f"watching {', '.join(map(str, paths))} every {interval}s (ctrl-c to stop)")
    while True:
        ready = []
        for f in trace_files(paths):
            st = f.stat()
            sig = (st.st_size, st.st_mtime)
            if seen.get(f) != sig and is_finished(f, idle_s):
                ready.append(f)
                seen[f] = sig
        if ready:
            t = load(client, db, ready)
            print(f"{datetime.now():%H:%M:%S} loaded {t['files']} file(s): {t['inserted']} events, "
                  f"{t['skipped']} already present, {t['rejected']} rejected")
        time.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path, help="trace files or directories")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--url", default=DEFAULT_URL, help="ClickHouse HTTP URL")
    ap.add_argument("--watch", action="store_true", help="keep running and load traces as runs finish")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between polls in --watch mode")
    ap.add_argument("--idle", type=float, default=600, help="load a trace without run_end after this many idle seconds")
    ap.add_argument("--batch-size", type=int, default=50_000)
    args = ap.parse_args()

    paths = args.paths or DEFAULT_PATHS
    client = connect(args.url)
    ensure_schema(client, args.db)
    if args.watch:
        try:
            watch(client, args.db, paths, args.interval, args.idle)
        except KeyboardInterrupt:
            return
    files = trace_files(paths)
    if not files:
        sys.exit("no trace files found")
    t = load(client, args.db, files, args.batch_size)
    print(f"loaded {t['files']} file(s) into {args.db}: {t['inserted']} events inserted, "
          f"{t['skipped']} already present, {t['rejected']} rejected")


if __name__ == "__main__":
    main()
