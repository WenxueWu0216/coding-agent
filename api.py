"""HTTP API for exploring agent sessions stored in ClickHouse.

  uvicorn api:app --port 8000

  GET /sessions                 finished sessions, newest first; keyset pagination and filters
  GET /sessions/{id}            one session's summary and its events in order
  GET /runs/compare?a=&b=       two eval runs side by side, with per-task regressions and fixes
  GET /export?session_id=|run_id=&format=csv|json   events, streamed
"""
import base64
import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import loader

SESSION_COLUMNS = ["session_id", "run_id", "task_id", "suite", "model", "started_at", "ended_at", "duration_ms",
                   "input_tokens", "output_tokens", "cost_usd", "tool_calls", "edits", "steps", "passed", "status",
                   "failure_type", "failure_root_cause"]
EVENT_COLUMNS = [c for c in loader.COLUMNS if c not in ("source_file",)]
EXPORT_BLOCK_ROWS = 10_000


def jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return v


def event_dict(cols: list[str], row: tuple) -> dict:
    d = {c: jsonable(v) for c, v in zip(cols, row)}
    if "attrs" in d:
        d["attrs"] = json.loads(d["attrs"] or "{}")
    return d


def encode_cursor(started_at: datetime, session_id: str) -> str:
    return base64.urlsafe_b64encode(f"{jsonable(started_at)}|{session_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        ts, sid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(ts.replace("Z", "+00:00")), sid
    except Exception:
        raise HTTPException(400, "invalid cursor") from None


def create_app(client=None, db: Optional[str] = None) -> FastAPI:
    db = db or os.environ.get("CLICKHOUSE_DB", loader.DEFAULT_DB)
    state: dict[str, Any] = {"client": client}

    def ch():
        if state["client"] is None:
            # A fixed session id would serialize concurrent requests ("session is locked").
            state["client"] = loader.connect(os.environ.get("CLICKHOUSE_URL", loader.DEFAULT_URL),
                                             autogenerate_session_id=False)
        return state["client"]

    app = FastAPI(title="Agent trace analytics")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["GET"])

    @app.get("/sessions")
    def list_sessions(
        limit: int = Query(50, ge=1, le=500),
        cursor: Optional[str] = None,
        status: Optional[Literal["passed", "failed", "completed"]] = None,
        since: Optional[datetime] = Query(None, description="started_at >= since (ISO-8601)"),
        until: Optional[datetime] = Query(None, description="started_at < until (ISO-8601)"),
        task_id: Optional[str] = None,
        model: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> dict:
        where, params = [], {}
        for name, value in (("status", status), ("task_id", task_id), ("model", model), ("run_id", run_id)):
            if value is not None:
                where.append(f"{name} = {{{name}:String}}")
                params[name] = value
        for name, value, op in (("since", since, ">="), ("until", until, "<")):
            if value is not None:
                where.append(f"started_at {op} {{{name}:DateTime64(3, 'UTC')}}")
                params[name] = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if cursor:
            # Keyset pagination: stable under concurrent inserts and cheap at any depth, unlike OFFSET.
            c_ts, c_id = decode_cursor(cursor)
            where.append("(started_at, session_id) < ({c_ts:DateTime64(3, 'UTC')}, {c_id:String})")
            params.update(c_ts=c_ts, c_id=c_id)
        sql = (f"SELECT {', '.join(SESSION_COLUMNS)} FROM {db}.sessions FINAL"
               f"{' WHERE ' + ' AND '.join(where) if where else ''}"
               f" ORDER BY started_at DESC, session_id DESC LIMIT {limit + 1}")
        rows = ch().query(sql, parameters=params).result_rows
        items = [dict(zip(SESSION_COLUMNS, map(jsonable, r))) for r in rows[:limit]]
        next_cursor = encode_cursor(rows[limit - 1][5], rows[limit - 1][0]) if len(rows) > limit else None
        return {"items": items, "next_cursor": next_cursor}

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        r = ch().query(f"SELECT {', '.join(EVENT_COLUMNS)} FROM {db}.events FINAL "
                       "WHERE session_id = {sid:String} ORDER BY ts, seq", parameters={"sid": session_id})
        if not r.result_rows:
            raise HTTPException(404, f"session {session_id!r} not found")
        s = ch().query(f"SELECT {', '.join(SESSION_COLUMNS)} FROM {db}.sessions FINAL "
                       "WHERE session_id = {sid:String}", parameters={"sid": session_id}).result_rows
        return {"session": dict(zip(SESSION_COLUMNS, map(jsonable, s[0]))) if s else None,
                "events": [event_dict(EVENT_COLUMNS, row) for row in r.result_rows]}

    @app.get("/runs/compare")
    def compare_runs(a: str, b: str) -> dict:
        summary = ch().query(
            f"""SELECT run_id, any(model), count(), countIf(passed IS NOT NULL), round(avg(passed), 4),
                       round(sum(cost_usd), 6), round(avg(cost_usd), 6), round(avg(input_tokens + output_tokens)),
                       quantile(0.5)(duration_ms)
                FROM {db}.sessions FINAL WHERE run_id IN ({{a:String}}, {{b:String}}) GROUP BY run_id""",
            parameters={"a": a, "b": b}).result_rows
        runs = {r[0]: dict(zip(["run_id", "model", "sessions", "judged", "pass_rate", "cost_usd",
                                "cost_per_session", "tokens_per_session", "p50_duration_ms"], r)) for r in summary}
        missing = [x for x in (a, b) if x not in runs]
        if missing:
            raise HTTPException(404, f"run(s) not found: {', '.join(missing)}")
        r = ch().query((loader.PROJECT_DIR / "queries" / "compare_runs.sql").read_text().replace("{db}", db),
                       parameters={"a": a, "b": b})
        tasks = [dict(zip(r.column_names, row)) for row in r.result_rows]
        return {"a": runs[a], "b": runs[b], "tasks": tasks,
                "regressions": [t["task_id"] for t in tasks if t["delta_pts"] < 0],
                "fixes": [t["task_id"] for t in tasks if t["delta_pts"] > 0]}

    @app.get("/export")
    def export(session_id: Optional[str] = None, run_id: Optional[str] = None,
               format: Literal["csv", "json"] = "json") -> StreamingResponse:
        if bool(session_id) == bool(run_id):
            raise HTTPException(400, "pass exactly one of session_id or run_id")
        key, value = ("session_id", session_id) if session_id else ("run_id", run_id)
        where = f"{key} = {{v:String}}"
        if not ch().query(f"SELECT 1 FROM {db}.events WHERE {where} LIMIT 1", parameters={"v": value}).result_rows:
            raise HTTPException(404, f"no events for {key} {value!r}")
        sql = f"SELECT {', '.join(EVENT_COLUMNS)} FROM {db}.events WHERE {where} ORDER BY session_id, ts, seq"

        def blocks() -> Iterator[list]:
            with ch().query_row_block_stream(sql, parameters={"v": value},
                                             settings={"max_block_size": EXPORT_BLOCK_ROWS}) as stream:
                yield from stream

        def as_csv() -> Iterator[str]:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(EVENT_COLUMNS)
            for block in blocks():
                for row in block:
                    w.writerow([jsonable(v) if v is not None else "" for v in row])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
            yield buf.getvalue()

        def as_json() -> Iterator[str]:
            yield "["
            first = True
            for block in blocks():
                parts = [json.dumps(event_dict(EVENT_COLUMNS, row)) for row in block]
                if parts:
                    yield ("" if first else ",") + ",".join(parts)
                    first = False
            yield "]"

        media = "text/csv" if format == "csv" else "application/json"
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", value)[:100] + f".{format}"
        return StreamingResponse(as_csv() if format == "csv" else as_json(), media_type=media,
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    return app


app = create_app()
