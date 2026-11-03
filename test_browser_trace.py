"""Browser traces: every step logged in the trace format, failures diagnosable from the trace alone,
repairs visible, and browser traces load into ClickHouse like any other trace."""
import json
from pathlib import Path

import pytest

import loader
import report
import workflow as wf
from browser_safety import BrowserPolicy
from test_workflow import FORM_DONE, ScriptedRepair, do_form, record
from tracing import browser_root_cause
from webapps.server import AppServer


@pytest.fixture(scope="module")
def server():
    with AppServer() as srv:
        yield srv


@pytest.fixture(scope="module")
def form_flow(server):
    return record(server, "form", do_form)


def check_form(page):
    text = page.inner_text("#result")
    return text == FORM_DONE, text[:200]


def replay(server, flow, mode, tmp_path, variant=None, **kw):
    server.set_variant("form", variant)
    try:
        path = tmp_path / f"{mode}.jsonl"
        result = wf.traced_replay(flow, BrowserPolicy(allow=[server.allow_entry]), mode, path, run_id="run-b",
                                  check=check_form, task_id="form_contact", wait_ms=600, **kw)
        return result, path, [json.loads(line) for line in path.read_text().splitlines()]
    finally:
        server.set_variant("form", None)


def test_every_step_is_traced_with_a_screenshot(server, form_flow, tmp_path):
    result, _, events = replay(server, form_flow, "fallback", tmp_path)
    assert result["passed"] and result["failure_root_cause"] == "none"
    assert [e["type"] for e in events] == ["run_start"] + ["browser_step"] * 7 + ["run_end"]
    steps = [e for e in events if e["type"] == "browser_step"]
    assert [s["action"] for s in steps] == ["open", "type", "type", "select", "check", "type", "click"]
    root = events[0]["event_id"]
    for s in steps:
        assert s["parent_event_id"] == root and s["status"] == "ok" and s["latency_ms"] >= 0
        assert s["tool_name"] == f"browser_{s['action']}" and s["run_id"] == "run-b"
        assert Path(s["screenshot_path"]).is_file()
    typed = steps[1]
    assert typed["target"]["name"] == "Full name" and typed["resolved_by"] == "selector"
    assert "value" not in typed and typed["value_size_bytes"] == len("Ada Lovelace")   # redacted by default
    end = events[-1]
    assert end["passed"] is True and end["steps_ok"] == 6 and end["stop"] == "done"


def test_failure_is_diagnosable_from_the_trace_alone(server, form_flow, tmp_path, capsys):
    result, path, events = replay(server, form_flow, "fallback", tmp_path, variant="renamed")
    assert not result["passed"] and result["failure_root_cause"] == "selector_not_found"
    failed = [e for e in events if e["type"] == "browser_step" and e["status"] == "error"]
    assert len(failed) == 1
    f = failed[0]
    assert f["step"] == 6 and f["action"] == "click" and f["error"] == "selector_not_found"
    assert f["target"] == {"selector": "#submit-btn", "role": "button", "name": "Submit", "text": "Submit"}
    assert [(a["strategy"], a["result"]) for a in f["attempts"]] == [("selector", "not_found"), ("role", "not_found"),
                                                                    ("text", "not_found")]
    assert f["url"] == server.url("form/")
    assert any(n.startswith('button "Send message" (#send)') for n in f["nearby"])   # what is there instead
    assert Path(f["screenshot_path"]).is_file() and f["screenshot_path"].endswith("-error.png")
    end = events[-1]
    assert end["failure_root_cause"] == "selector_not_found" and end["steps_ok"] == 5

    report.show_trace_file(path)
    out = capsys.readouterr().out
    assert "BROWSER step 6 click button 'Submit' -> ERROR[selector_not_found]" in out
    assert "tried: selector '#submit-btn' -> not_found (0); role \"button 'Submit'\" -> not_found (0)" in out
    assert 'on page: button "Send message" (#send)' in out and "screenshot:" in out
    assert "root_cause=selector_not_found" in out


def test_repair_is_traced_and_reported(server, form_flow, tmp_path, capsys):
    result, path, events = replay(server, form_flow, "repair", tmp_path, variant="renamed",
                                  client=ScriptedRepair("Send message", 0.9), model="claude-haiku-5-5")
    assert result["passed"] and result["repairs"] == 1
    kinds = [e["type"] for e in events]
    assert kinds.index("model_call") < kinds.index("browser_repair") < len(kinds) - 2
    repair = events[kinds.index("browser_repair")]
    step6 = events[kinds.index("browser_repair") + 1]
    assert step6["step"] == 6 and step6["resolved_by"] == "model" and step6["repair_event_id"] == repair["event_id"]
    assert events[-1]["input_tokens"] == 1200 and events[-1]["cost_usd"] > 0
    report.show_trace_file(path)
    out = capsys.readouterr().out
    assert "REPAIR step 6 click button 'Submit' -> button 'Send message': accepted  confidence 0.90" in out
    assert "-> ok via model" in out and "REPAIRED" in out


def test_root_cause_rules():
    assert browser_root_cause([], True) == "none"
    assert browser_root_cause([{"type": "browser_step", "status": "ok"}], False) == "wrong_result"
    assert browser_root_cause([{"type": "browser_step", "status": "error", "error": "needs_approval"}],
                              False) == "needs_approval"


def test_browser_traces_load_into_clickhouse(ch, server, form_flow, tmp_path):
    client, db = ch
    replay(server, form_flow, "fallback", tmp_path, variant="renamed")
    stats = loader.load(client, db, loader.trace_files([tmp_path]), log=lambda *_: None)
    assert stats["rejected"] == 0
    rows = client.query(f"SELECT step, tool_name, status, error, JSONExtractString(attrs, 'resolved_by'), "
                        f"JSONLength(attrs, 'attempts') FROM {db}.events WHERE type = 'browser_step' "
                        f"ORDER BY seq").result_rows
    assert len(rows) == 7
    assert rows[-1] == (6, "browser_click", "error", "selector_not_found", "", 3)
    assert rows[1][:5] == (1, "browser_type", "ok", "", "selector")
    session = client.query(f"SELECT passed, failure_root_cause FROM {db}.sessions").result_rows
    assert session == [(0, "selector_not_found")]
