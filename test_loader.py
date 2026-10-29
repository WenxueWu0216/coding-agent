"""Loader: validation of bad rows (no server needed) and idempotent loading (real ClickHouse)."""
import json

import pytest

import eval as ev
import loader
from test_eval import FIX, TASK, FakeClient, text


def make_trace(path, script=FIX, run_id="run-1"):
    ev.run_task(TASK, "claude-haiku-5-5", 20, client=FakeClient(script), trace_path=path, run_id=run_id)
    return [json.loads(l) for l in path.read_text().splitlines()]


def write(path, events):
    path.write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n")


@pytest.mark.parametrize("change, reason", [
    ({"event_id": ""}, "event_id must be a non-empty string"),
    ({"session_id": None}, "session_id must be a non-empty string"),
    ({"type": "teleport"}, "unknown event type 'teleport'"),
    ({"input_tokens": -5}, "input_tokens is negative (-5)"),
    ({"output_tokens": "12"}, "output_tokens must be an integer, got str"),
    ({"latency_ms": True}, "latency_ms must be an integer, got bool"),
    ({"cost_usd": -0.1}, "cost_usd must be a non-negative number"),
    ({"ts": "yesterday"}, "ts is not ISO-8601"),
    ({"ts": "2026-10-29T10:00:00"}, "ts has no timezone"),
    ({"passed": "yes"}, "passed must be a boolean or null"),
])
def test_bad_rows_are_rejected_with_reason(tmp_path, change, reason):
    events = make_trace(tmp_path / "t.jsonl")
    events[2] = {**events[2], **change}
    write(tmp_path / "bad.jsonl", events)
    batch = loader.Batch()
    loader.read_file(tmp_path / "bad.jsonl", batch)
    assert len(batch.rows) == len(events) - 1
    assert len(batch.rejects) == 1 and batch.rejects[0].line == 3
    assert reason in batch.rejects[0].reason
    assert str(batch.rejects[0]).startswith(f"{tmp_path / 'bad.jsonl'}:3: ")


def test_invalid_json_and_legacy_rows(tmp_path):
    events = make_trace(tmp_path / "t.jsonl")
    write(tmp_path / "bad.jsonl", [events[0], "{not json", '{"t_ms": 0, "type": "task_start", "task": "x"}',
                                   *events[1:]])
    batch = loader.Batch()
    loader.read_file(tmp_path / "bad.jsonl", batch)
    assert [(r.line, r.reason.split(":")[0]) for r in batch.rejects] == [(2, "invalid JSON"), (3, "missing run_id")]
    assert len(batch.rows) == len(events)


def test_rows_carry_task_and_attrs(tmp_path):
    events = make_trace(tmp_path / "t.jsonl")
    batch = loader.Batch()
    loader.read_file(tmp_path / "t.jsonl", batch)
    rows = [dict(zip(loader.COLUMNS, r)) for r in batch.rows]
    assert {r["task_id"] for r in rows} == {"01_last_n"} and {r["suite"] for r in rows} == {"core"}
    end = rows[-1]
    assert end["type"] == "run_end" and end["passed"] == 1 and end["failure_root_cause"] == "none"
    assert json.loads(end["attrs"])["tool_calls"] == 2
    assert len(rows) == len(events)


def test_is_finished(tmp_path):
    events = make_trace(tmp_path / "t.jsonl")
    write(tmp_path / "running.jsonl", events[:-1])
    assert loader.is_finished(tmp_path / "t.jsonl", idle_s=600)
    assert not loader.is_finished(tmp_path / "running.jsonl", idle_s=600)
    assert loader.is_finished(tmp_path / "running.jsonl", idle_s=-1)


# ---------- against a real ClickHouse ----------

def count(client, db, sql):
    return client.query(sql.format(db=db)).result_rows[0][0]


def test_reload_is_idempotent(ch, tmp_path):
    client, db = ch
    make_trace(tmp_path / "a.jsonl")
    make_trace(tmp_path / "b.jsonl", script=[([text("Looks fine.")], "end_turn")])
    files = loader.trace_files([tmp_path])
    first = loader.load(client, db, files, log=lambda *_: None)
    again = loader.load(client, db, files, log=lambda *_: None)
    assert first["inserted"] > 0 and first["skipped"] == 0
    assert again == {**first, "inserted": 0, "skipped": first["inserted"]}
    assert count(client, db, "SELECT count() FROM {db}.events") == first["inserted"]
    assert count(client, db, "SELECT uniqExact(event_id) FROM {db}.events") == first["inserted"]
    assert count(client, db, "SELECT count() FROM {db}.sessions") == 2   # the MV saw each run_end once
    statuses = client.query(f"SELECT status, failure_root_cause FROM {db}.sessions ORDER BY status").result_rows
    assert statuses == [("failed", "premature_finish"), ("passed", "none")]


def test_partial_file_then_complete(ch, tmp_path):
    client, db = ch
    events = make_trace(tmp_path / "full.jsonl")
    write(tmp_path / "run.jsonl", events[:4])
    loader.load(client, db, [tmp_path / "run.jsonl"], log=lambda *_: None)
    write(tmp_path / "run.jsonl", events)
    t = loader.load(client, db, [tmp_path / "run.jsonl"], log=lambda *_: None)
    assert t["inserted"] == len(events) - 4 and t["skipped"] == 4
    assert count(client, db, "SELECT count() FROM {db}.events") == len(events)


def test_bad_rows_do_not_abort_batch(ch, tmp_path):
    client, db = ch
    events = make_trace(tmp_path / "t.jsonl")
    events[3] = {**events[3], "input_tokens": -1}
    write(tmp_path / "t.jsonl", events)
    logged = []
    t = loader.load(client, db, [tmp_path / "t.jsonl"], log=logged.append)
    assert t["inserted"] == len(events) - 1 and t["rejected"] == 1
    assert logged == [f"rejected {tmp_path / 't.jsonl'}:4: input_tokens is negative (-1)"]
    assert client.query(f"SELECT line, reason FROM {db}.rejected_rows").result_rows == [
        (4, "input_tokens is negative (-1)")]


def test_ingested_at_supports_freshness(ch, tmp_path):
    client, db = ch
    make_trace(tmp_path / "t.jsonl")
    loader.load(client, db, [tmp_path / "t.jsonl"], log=lambda *_: None)
    lag = count(client, db, "SELECT min(dateDiff('millisecond', ts, ingested_at)) FROM {db}.events")
    assert 0 <= lag < 60_000
