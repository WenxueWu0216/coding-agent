"""API tests against a real ClickHouse loaded with harness-generated traces (scripted fake model)."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import eval as ev
import loader
from api import create_app
from test_eval import FIX, TASK, FakeClient, text

LAZY = [([text("Looks fine.")], "end_turn")]
# (run_id, model, script): run-a passes 2 of 3, run-b passes 1 of 3 (a regression on 01_last_n)
RUNS = [("run-a", "claude-haiku-5-5", [FIX, FIX, LAZY]),
        ("run-b", "claude-sonnet-5-5", [LAZY, FIX, LAZY])]


@pytest.fixture(scope="module")
def api(ch_module, tmp_path_factory):
    client, db = ch_module
    d = tmp_path_factory.mktemp("traces")
    tasks = [TASK, ev.load_tasks(["05_fizzbuzz"])[0], ev.load_tasks(["09_celsius"])[0]]
    sessions = {}
    for run_id, model, scripts in RUNS:
        for task, script in zip(tasks, scripts):
            if task is not TASK and script is FIX:   # FIX only fixes 01_last_n; use an oracle elsewhere
                from test_eval import OracleClient
                fake = OracleClient(task)
            else:
                fake = FakeClient(script)
            path = d / f"{run_id}__{task['id']}.jsonl"
            row = ev.run_task(task, model, 20, client=fake, trace_path=path, run_id=run_id)
            sessions[(run_id, task["id"])] = row
    loader.load(client, db, loader.trace_files([d]), log=lambda *_: None)
    return TestClient(create_app(client, db)), sessions


def test_list_sessions_paginates_without_gaps(api):
    http, sessions = api
    seen, cursor, pages = [], None, 0
    while True:
        r = http.get("/sessions", params={"limit": 4, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200
        body = r.json()
        seen += [s["session_id"] for s in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 2 and len(seen) == len(set(seen)) == 6
    assert set(seen) == {s["session_id"] for s in sessions.values()}
    started = [s for s in http.get("/sessions", params={"limit": 6}).json()["items"]]
    assert [s["started_at"] for s in started] == sorted((s["started_at"] for s in started), reverse=True)


def test_list_sessions_filters(api):
    http, sessions = api

    def ids(**params):
        return {s["session_id"] for s in http.get("/sessions", params=params).json()["items"]}

    assert ids(status="passed") == {s["session_id"] for s in sessions.values() if s["passed"]}
    assert len(ids(status="failed")) == 3
    assert ids(task_id="01_last_n") == {sessions[(r, "01_last_n")]["session_id"] for r in ("run-a", "run-b")}
    assert ids(model="claude-sonnet-5-5") == {s["session_id"] for (r, _), s in sessions.items() if r == "run-b"}
    assert ids(run_id="run-a", status="passed") == {sessions[("run-a", t)]["session_id"]
                                                    for t in ("01_last_n", "05_fizzbuzz")}
    now = datetime.now(timezone.utc)
    assert len(ids(since=(now - timedelta(hours=1)).isoformat())) == 6
    assert ids(since=(now + timedelta(hours=1)).isoformat()) == set()
    assert ids(until=(now - timedelta(hours=1)).isoformat()) == set()


def test_list_sessions_rejects_bad_input(api):
    http, _ = api
    assert http.get("/sessions", params={"status": "exploded"}).status_code == 422
    assert http.get("/sessions", params={"limit": 0}).status_code == 422
    assert http.get("/sessions", params={"cursor": "%%%"}).status_code == 400


def test_get_session_returns_ordered_events(api):
    http, sessions = api
    sid = sessions[("run-a", "01_last_n")]["session_id"]
    body = http.get(f"/sessions/{sid}").json()
    types = [e["type"] for e in body["events"]]
    assert types[0] == "run_start" and types[-1] == "run_end" and "tool_result" in types
    assert [e["ts"] for e in body["events"]] == sorted(e["ts"] for e in body["events"])
    assert body["session"]["status"] == "passed" and body["session"]["tool_calls"] == 2
    assert body["events"][-1]["attrs"]["tool_calls"] == 2


def test_unknown_session_is_404(api):
    http, _ = api
    r = http.get("/sessions/does-not-exist")
    assert r.status_code == 404 and "not found" in r.json()["detail"]


def test_compare_runs(api):
    http, _ = api
    body = http.get("/runs/compare", params={"a": "run-a", "b": "run-b"}).json()
    assert body["a"]["sessions"] == 3 and body["a"]["pass_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert body["b"]["pass_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert body["regressions"] == ["01_last_n"] and body["fixes"] == []
    assert len(body["tasks"]) == 3
    assert http.get("/runs/compare", params={"a": "run-a", "b": "nope"}).status_code == 404


def test_export_csv_and_json(api):
    http, sessions = api
    sid = sessions[("run-a", "01_last_n")]["session_id"]
    n = len(http.get(f"/sessions/{sid}").json()["events"])

    r = http.get("/export", params={"session_id": sid, "format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == n and {row["session_id"] for row in rows} == {sid}

    r = http.get("/export", params={"session_id": sid, "format": "json"})
    events = json.loads(r.text)
    assert len(events) == n and events[0]["type"] == "run_start"

    with http.stream("GET", "/export", params={"run_id": "run-b", "format": "json"}) as r:
        body = "".join(r.iter_text())
    assert {e["run_id"] for e in json.loads(body)} == {"run-b"}


def test_export_errors(api):
    http, _ = api
    assert http.get("/export", params={"session_id": "nope"}).status_code == 404
    assert http.get("/export").status_code == 400
    assert http.get("/export", params={"session_id": "a", "run_id": "b"}).status_code == 400
    assert http.get("/export", params={"session_id": "a", "format": "xml"}).status_code == 422
