"""Browser eval harness: the six tasks in all three modes, with a scripted repair model."""
import json
from types import SimpleNamespace as NS

import browser_eval as be
import report
from test_workflow import ScriptedRepair


def test_six_tasks_two_of_them_changed():
    tasks = be.load_browser_tasks()
    assert len(tasks) == 6
    assert sorted(t["id"] for t in tasks if t["category"] == "changed") == ["b5_form_renamed_button",
                                                                            "b6_wizard_moved_buttons"]
    assert {t["app"] for t in tasks} == {"form", "wizard", "table"}


def test_suite_scores_each_mode(tmp_path, monkeypatch, capsys):
    run_dir = tmp_path / "run"
    rows = be.run_browser_suite(be.load_browser_tasks(), be.MODES, "claude-haiku-5-5",
                                ScriptedRepair("Send message", 0.9), run_dir, "run-x", log=lambda *_: None)
    by = {(r["task_id"], r["mode"]): r for r in rows}
    assert len(rows) == 18
    passed = {m: sorted(t for (t, mode), r in by.items() if mode == m and r["passed"]) for m in be.MODES}
    unchanged = ["b1_form_contact", "b2_wizard_signup", "b3_table_search", "b4_wizard_back_edit"]
    assert passed["selector"] == unchanged
    assert passed["fallback"] == unchanged + ["b6_wizard_moved_buttons"]
    assert passed["repair"] == unchanged + ["b5_form_renamed_button", "b6_wizard_moved_buttons"]
    assert by[("b5_form_renamed_button", "selector")]["failed_step"] == 6
    assert by[("b6_wizard_moved_buttons", "selector")]["failed_step"] == 3
    assert by[("b5_form_renamed_button", "repair")]["repairs_accepted"] == 1
    assert sum(r["repairs"] for r in rows) == 1                     # only the renamed button needed the model
    assert (run_dir / "workflows" / "b5_form_renamed_button__repair__r0.repaired.json").is_file()
    assert len(list((run_dir / "traces").glob("*.jsonl"))) == 18

    table = be.summarize_browser(rows, out=lambda *_: None)
    assert (table["selector"]["pass"], table["fallback"]["pass"], table["repair"]["pass"]) == (4, 5, 6)
    assert table["repair"]["changed"] == "2/2" and table["fallback"]["changed"] == "1/2"

    # the run is readable by report.py like any other eval run
    (run_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (run_dir / "manifest.json").write_text(json.dumps(be.manifest("run-x", "m", be.MODES, 1, be.load_browser_tasks())))
    report.summary(run_dir)
    out = capsys.readouterr().out
    assert "15/18 passed" in out and "selector_not_found" in out
    assert "--trace b5_form_renamed_button__fallback" in out or "--trace b5_form_renamed_button__selector" in out
    report.show_trace(run_dir, "b5_form_renamed_button__selector")
    assert "BROWSER step 6 click button 'Submit' -> ERROR[selector_not_found]" in capsys.readouterr().out


def test_a_broken_task_definition_is_caught_at_record_time(tmp_path):
    from browser_safety import BrowserPolicy
    from webapps.server import AppServer
    task = {**be.load_browser_tasks(["b1"])[0], "expect": [{"selector": "#result", "equals": "something else"}]}
    with AppServer() as srv:
        try:
            be.record_task(task, srv, BrowserPolicy(allow=[srv.allow_entry]))
        except RuntimeError as e:
            assert "did not reach the expected state" in str(e)
        else:
            raise AssertionError("expected RuntimeError")


def test_eval_client_reads_eval_key(monkeypatch):
    import llm_client
    seen = {}
    monkeypatch.setattr(llm_client.anthropic, "Anthropic", lambda **kw: seen.update(kw) or NS(**kw))
    monkeypatch.setenv("EVAL_ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy.invalid")
    llm_client.make_client()
    assert seen == {"api_key": "test-key-not-real", "base_url": "https://api.anthropic.com"}
    monkeypatch.delenv("EVAL_ANTHROPIC_API_KEY")
    seen.clear()
    llm_client.make_client()
    assert seen == {}                      # SDK defaults
