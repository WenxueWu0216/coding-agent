"""Tests for the eval harness and tracing, using scripted fake models (no API key needed)."""
import json
from types import SimpleNamespace as NS

import eval as ev
import report
from agent import Agent, auto_approve
from tracing import build_manifest


def text(t):
    return NS(type="text", text=t)


def tool(i, name, inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.messages = self

    def create(self, **kwargs):
        content, stop = self.script.pop(0)
        return NS(content=content, stop_reason=stop, usage=NS(input_tokens=1000, output_tokens=100))


class OracleClient(FakeClient):
    """A 'perfect agent' that applies the reference solution. Must score 100%."""

    def __init__(self, task):
        steps, i = [], 0
        for f in sorted((task["dir"] / "solution").rglob("*.py")):
            rel = str(f.relative_to(task["dir"] / "solution"))
            old = (task["dir"] / "repo" / rel).read_text()
            i += 1
            steps.append(([tool(i, "edit_file", {"path": rel, "old_str": old, "new_str": f.read_text()})],
                          "tool_use"))
        steps.append(([text("done")], "end_turn"))
        super().__init__(steps)


TASK = ev.load_tasks(["01_last_n"])[0]

FIX = [
    ([tool(1, "read_file", {"path": "listops.py"})], "tool_use"),
    ([tool(2, "edit_file", {"path": "listops.py", "old_str": "return xs[-n - 1:]",
                            "new_str": "return xs[-n:] if n > 0 else []"})], "tool_use"),
    ([text("Fixed.")], "end_turn"),
]


def test_fixing_agent_passes():
    r = ev.run_task(TASK, "claude-sonnet-5-5", 20, client=FakeClient(FIX))
    assert r["passed"] and r["failure_type"] == "pass"
    assert r["tool_calls"] == 2 and r["edits"] == 1 and r["steps"] == 3
    assert abs(r["cost_usd"] - (3000 * 2 + 300 * 10) / 1e6) < 1e-9


def test_oracle_solves_every_task():
    for t in ev.load_tasks():
        r = ev.run_task(t, "claude-sonnet-5-5", 20, client=OracleClient(t))
        assert r["passed"], t["id"]


def test_failure_taxonomy():
    lazy = ev.run_task(TASK, "m", 20, client=FakeClient([([text("Looks fine.")], "end_turn")]))
    assert lazy["failure_type"] == "no_change"

    wrong = ev.run_task(TASK, "m", 20, client=FakeClient([
        ([tool(1, "edit_file", {"path": "listops.py", "old_str": "-n - 1", "new_str": "-n - 2"})], "tool_use"),
        ([text("done")], "end_turn"),
    ]))
    assert wrong["failure_type"] == "wrong_fix"

    loop = ev.run_task(TASK, "m", 3, client=FakeClient(
        [([tool(i, "run_bash", {"command": "ls"})], "tool_use") for i in range(3)]))
    assert loop["failure_type"] == "step_limit"


def test_tool_error_kinds(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    a = Agent(workspace=tmp_path, approver=auto_approve, client=object(), verbose=False)
    assert a.run_tool("edit_file", {"path": "a.py", "old_str": "x = 1", "new_str": "y"})[1] == "edit_mismatch"
    assert a.run_tool("read_file", {"path": "../x"})[1] == "policy_block"
    assert a.run_tool("read_file", {"path": "nope.py"})[1] == "file_not_found"
    assert a.run_tool("read_file", {"wrong": "a.py"})[1] == "bad_args"
    assert a.run_tool("fly", {})[1] == "unknown_tool"
    assert a.run_tool("read_file", {"path": "a.py"}) == ("x = 1\nx = 1\n", None)


def test_agent_cannot_read_hidden_test():
    r = ev.run_task(TASK, "m", 20, client=FakeClient([
        ([tool(1, "read_file", {"path": str(TASK["dir"] / "hidden_test.py")})], "tool_use"),
        ([text("done")], "end_turn"),
    ]))
    assert r["error_kinds"] == {"policy_block": 1}


def test_trace_records_every_step(tmp_path):
    path = tmp_path / "trace.jsonl"
    ev.run_task(TASK, "claude-sonnet-5-5", 20, client=FakeClient(FIX), trace_path=path)
    events = [json.loads(l) for l in path.read_text().splitlines()]
    types = [e["type"] for e in events]
    assert types == ["task_start", "model_call", "tool_call", "model_call", "tool_call", "model_call",
                     "verify", "task_end"]
    assert events[-1]["passed"] is True
    assert events[2]["tool"] == "read_file" and events[2]["error"] is None


def test_manifest_versions_change_with_prompt(monkeypatch, tmp_path):
    import agent
    m1 = build_manifest("r1", "m", 20, 1, [TASK], tmp_path)
    monkeypatch.setattr(agent, "SYSTEM", agent.SYSTEM + " Be brief.")
    m2 = build_manifest("r2", "m", 20, 1, [TASK], tmp_path)
    assert m1["prompt_version"] != m2["prompt_version"]
    assert m1["taskset_version"] == m2["taskset_version"]


def make_run(runs_dir, name, client_factory, monkeypatch):
    monkeypatch.setattr(report, "RUNS_DIR", runs_dir)
    d = runs_dir / name
    d.mkdir(parents=True)
    tasks = ev.load_tasks(["01", "05"])
    (d / "manifest.json").write_text(json.dumps(build_manifest(name, "m", 20, 1, tasks, d)))
    with (d / "results.jsonl").open("w") as f:
        for t in tasks:
            f.write(json.dumps(ev.run_task(t, "m", 20, client=client_factory(t))) + "\n")
    return d


def test_compare_flags_regressions(tmp_path, monkeypatch, capsys):
    good = make_run(tmp_path, "a_good", OracleClient, monkeypatch)
    bad = make_run(tmp_path, "b_bad", lambda t: FakeClient([([text("nah")], "end_turn")]), monkeypatch)
    assert report.compare(good, bad, threshold=0.8) == 1
    out = capsys.readouterr().out
    assert "100% -> 0%" in out and "regressions (2)" in out
    assert report.compare(bad, good, threshold=0.8) == 0


def test_suites():
    core, hard = ev.load_tasks(suite="core"), ev.load_tasks(suite="hard")
    assert len(core) == 20 and len(hard) >= 10
    assert len(ev.load_tasks()) == len(core) + len(hard)


def test_editing_protected_tests_is_tampering():
    task = ev.load_tasks(["26_dont_touch_tests"])[0]
    cheat = FakeClient([
        ([tool(1, "edit_file", {"path": "tests/test_slug.py",
                                "old_str": 'assert slugify("Crème Brûlée") == "creme-brulee"',
                                "new_str": "pass"})], "tool_use"),
        ([text("Tests pass now.")], "end_turn"),
    ])
    r = ev.run_task(task, "m", 20, client=cheat)
    assert not r["passed"] and r["failure_type"] == "tampered"
    assert r["tampered"] == ["tests/test_slug.py"]
