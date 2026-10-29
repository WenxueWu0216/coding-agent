"""Trace schema, redaction, --trace-content, and failure root-cause rules (no API key needed)."""
import json
import re

import eval as ev
import report
from test_eval import FIX, TASK, FakeClient, text, tool
from tracing import ENVELOPE, EVENT_TYPES, Tracer, failure_root_cause, scrub

SECRET_FIX = [
    ([tool(1, "read_file", {"path": "listops.py"})], "tool_use"),
    ([tool(2, "edit_file", {"path": "listops.py", "old_str": "return xs[-n - 1:]",
                            "new_str": "return xs[-n:] if n > 0 else []  # api_key=sk-ant-abcdef123456"})],
     "tool_use"),
    ([text("Fixed.")], "end_turn"),
]


def run(tmp_path, script, trace_content=False, max_steps=20, task=TASK):
    path = tmp_path / "t.jsonl"
    row = ev.run_task(task, "claude-haiku-5-5", max_steps, client=FakeClient(script), trace_path=path,
                      run_id="run-1", trace_content=trace_content)
    return row, [json.loads(l) for l in path.read_text().splitlines()], path.read_text()


def test_every_event_has_the_envelope(tmp_path):
    row, events, _ = run(tmp_path, FIX)
    ids = {e["event_id"] for e in events}
    assert len(ids) == len(events)
    for e in events:
        assert set(ENVELOPE) <= set(e), e["type"]
        assert e["type"] in EVENT_TYPES
        assert e["run_id"] == "run-1" and e["session_id"] == row["session_id"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", e["ts"])
        assert e["model"] == "claude-haiku-5-5"
        assert e["parent_event_id"] is None or e["parent_event_id"] in ids
    assert events[0]["parent_event_id"] is None


def test_parent_links_and_cost(tmp_path):
    _, events, _ = run(tmp_path, FIX)
    by_id = {e["event_id"]: e for e in events}
    for e in events:
        if e["type"] == "tool_result":
            call = by_id[e["parent_event_id"]]
            assert call["type"] == "tool_call" and call["tool_name"] == e["tool_name"]
        if e["type"] == "tool_call":
            assert by_id[e["parent_event_id"]]["type"] == "model_call"
        if e["type"] == "approval_decision":
            assert by_id[e["parent_event_id"]]["tool_name"] == "edit_file" and e["decision"] == "approved"
    model_calls = [e for e in events if e["type"] == "model_call"]
    assert all(abs(e["cost_usd"] - (1000 * 0.10 + 100 * 0.50) / 1e6) < 1e-12 for e in model_calls)
    end = events[-1]
    assert end["type"] == "run_end" and end["task_id"] == "01_last_n" and end["suite"] == "core"
    assert end["passed"] is True and end["failure_type"] == "pass" and end["failure_root_cause"] == "none"
    assert end["input_tokens"] == 3000 and end["output_tokens"] == 300 and end["tool_calls"] == 2
    assert abs(end["cost_usd"] - sum(e["cost_usd"] for e in model_calls)) < 1e-12


def test_default_trace_is_redacted(tmp_path):
    _, events, raw = run(tmp_path, SECRET_FIX)
    assert "return xs" not in raw and "sk-ant" not in raw and "listops.py" not in raw
    assert "last_n()" not in raw   # the task prompt is content too
    call = next(e for e in events if e["type"] == "tool_call" and e["tool_name"] == "edit_file")
    assert "args" not in call and len(call["tool_args_hash"]) == 16 and call["args_size_bytes"] > 50
    result = next(e for e in events if e["type"] == "tool_result")
    assert "output" not in result and result["output_size_bytes"] > 0 and result["output_hash"]


def test_trace_content_keeps_args_but_masks_secrets(tmp_path):
    _, events, raw = run(tmp_path, SECRET_FIX, trace_content=True)
    call = next(e for e in events if e["type"] == "tool_call" and e["tool_name"] == "edit_file")
    assert call["args"]["path"] == "listops.py" and "return xs[-n:]" in call["args"]["new_str"]
    assert "sk-ant-abcdef" not in raw and "[REDACTED]" in raw
    assert scrub("password: hunter2 ok") == "password: [REDACTED] ok"


def test_report_trace_works_in_both_modes(tmp_path, monkeypatch, capsys):
    for mode in (False, True):
        d = tmp_path / f"run{mode}"
        (d / "traces").mkdir(parents=True)
        ev.run_task(TASK, "m", 20, client=FakeClient(FIX), trace_path=d / "traces" / "01_last_n__r0.jsonl",
                    trace_content=mode)
        report.show_trace(d, "01_last_n")
        out = capsys.readouterr().out
        assert "TOOL   edit_file" in out and "END    passed=True" in out
        assert ("return xs[-n:]" in out) == mode
        assert ("redacted" in out) != mode


def test_report_reads_legacy_traces(tmp_path, capsys):
    (tmp_path / "traces").mkdir()
    legacy = [{"t_ms": 0, "type": "task_start", "task": "x", "prompt": "fix it"},
              {"t_ms": 5, "type": "tool_call", "step": 1, "tool": "ls", "args": {}, "latency_ms": 1,
               "error": None, "output": "a.py"},
              {"t_ms": 9, "type": "task_end", "passed": False, "failure_type": "wrong_fix", "cost_usd": 0.1}]
    (tmp_path / "traces" / "x__r0.jsonl").write_text("\n".join(json.dumps(e) for e in legacy))
    report.show_trace(tmp_path, "x")
    out = capsys.readouterr().out
    assert "TASK   fix it" in out and "-> 'a.py'" in out and "wrong_fix" in out


# ---------- root-cause rules ----------

def edit(i, old, new, path="listops.py"):
    return ([tool(i, "edit_file", {"path": path, "old_str": old, "new_str": new})], "tool_use")


def test_root_cause_looping_same_call(tmp_path):
    row, events, _ = run(tmp_path, [([tool(i, "run_bash", {"command": "ls"})], "tool_use") for i in range(5)],
                         max_steps=5)
    assert row["failure_type"] == "step_limit" and row["failure_root_cause"] == "looping_edits"
    assert events[-1]["failure_root_cause"] == "looping_edits"


def test_root_cause_looping_same_file(tmp_path):
    script = [edit(i, f"-n - {i}" if i else "-n - 1", f"-n - {i + 1}") for i in range(4)]
    row, _, _ = run(tmp_path, script + [([text("done")], "end_turn")])
    assert row["edits"] == 4 and row["failure_root_cause"] == "looping_edits"


def test_root_cause_premature_finish_without_edits(tmp_path):
    row, _, _ = run(tmp_path, [([text("Looks fine to me.")], "end_turn")])
    assert row["failure_type"] == "no_change" and row["failure_root_cause"] == "premature_finish"


def test_root_cause_bad_file_context(tmp_path):
    script = [edit(1, "this text is not in the file", "x"),
              ([tool(2, "read_file", {"path": "list_ops.py"})], "tool_use"),
              edit(3, "-n - 1", "-n - 2"),
              ([text("done")], "end_turn")]
    row, _, _ = run(tmp_path, script)
    assert row["failure_type"] == "wrong_fix" and row["error_kinds"] == {"edit_mismatch": 1, "file_not_found": 1}
    assert row["failure_root_cause"] == "bad_file_context"


def test_root_cause_other(tmp_path):
    row, _, _ = run(tmp_path, [edit(1, "-n - 1", "-n - 2"), ([text("done")], "end_turn")])
    assert row["failure_type"] == "wrong_fix" and row["failure_root_cause"] == "other"


def test_root_cause_failing_gate_is_premature():
    events = [{"type": "tool_call", "event_id": "c", "tool_name": "edit_file", "tool_args_hash": "h",
               "target_hash": "f"},
              {"type": "tool_result", "event_id": "r", "parent_event_id": "c", "status": "ok", "error": None},
              {"type": "verify", "ok": False}]
    assert failure_root_cause(events, False, "rolled_back") == "premature_finish"
    assert failure_root_cause(events, False, "step_limit") == "other"
    assert failure_root_cause(events, True, "done") == "none"


def test_cli_session_writes_run_start_and_end(tmp_path):
    from cli import Session

    class FakeAgent:
        model = "claude-haiku-5-5"

        def run(self, messages, tracer=None):
            tracer.event("model_call", parent=tracer.root_id, input_tokens=10, output_tokens=2)
            return {"input_tokens": 10, "output_tokens": 2}

    tracer = Tracer(tmp_path / "s.jsonl", run_id="cli-x", model="claude-haiku-5-5")
    s = Session(FakeAgent(), tracer=tracer, out=lambda *a: None)
    s.turn("hello")
    s.close()
    events = [json.loads(l) for l in (tmp_path / "s.jsonl").read_text().splitlines()]
    assert [e["type"] for e in events] == ["run_start", "model_call", "run_end"]
    assert events[-1]["input_tokens"] == 10 and events[-1]["passed"] is None
