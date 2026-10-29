"""Tests for the completion gate: lint/test on finish, feedback, and rollback."""
from types import SimpleNamespace as NS

import eval as ev
from agent import Agent, auto_approve
from verify import Verifier, run_tests


def text(t):
    return NS(type="text", text=t)


def tool(i, name, inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


class FakeClient:
    """Replays scripted responses and records every request it receives."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot history
        content, stop = self.script.pop(0)
        return NS(content=content, stop_reason=stop, usage=NS(input_tokens=100, output_tokens=10))


def edit(i, path, old, new):
    return ([tool(i, "edit_file", {"path": path, "old_str": old, "new_str": new})], "tool_use")


DONE = ([text("Done.")], "end_turn")


def make_repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    return tmp_path


def agent_for(repo, script):
    client = FakeClient(script)
    return Agent(workspace=repo, approver=auto_approve, client=client, verbose=False), client


def test_clean_edit_passes_gate(tmp_path):
    repo = make_repo(tmp_path)
    agent, _ = agent_for(repo, [edit(1, "calc.py", "a + b", "b + a"), DONE])
    stats = agent.run([{"role": "user", "content": "refactor"}])
    assert stats["stop"] == "done" and stats["verify_runs"] == 1 and stats["verify_failures"] == 0


def test_broken_edit_is_fed_back_and_fixed(tmp_path):
    repo = make_repo(tmp_path)
    agent, client = agent_for(repo, [
        edit(1, "calc.py", "a + b", "a - b"), DONE,          # breaks the test, claims done
        edit(2, "calc.py", "a - b", "a + b"), DONE,          # sees the failure, fixes it
    ])
    messages = [{"role": "user", "content": "change add"}]
    stats = agent.run(messages)
    assert stats["stop"] == "done" and stats["verify_runs"] == 2 and stats["verify_failures"] == 1
    feedback = client.requests[2]["messages"][-1]["content"]
    assert "Automatic verification" in feedback and "test_calc.py::test_add" in feedback


def test_lint_error_counts_as_failure(tmp_path):
    repo = make_repo(tmp_path)
    agent, client = agent_for(repo, [
        edit(1, "calc.py", "return a + b", "return a + c"), DONE,   # undefined name
        edit(2, "calc.py", "return a + c", "return a + b"), DONE,
    ])
    stats = agent.run([{"role": "user", "content": "x"}])
    assert stats["verify_failures"] == 1 and stats["stop"] == "done"


def test_rollback_after_repeated_failures(tmp_path):
    repo = make_repo(tmp_path)
    original = (repo / "calc.py").read_text()
    agent, client = agent_for(repo, [
        edit(1, "calc.py", "a + b", "a - b"),
        ([tool(2, "edit_file", {"path": "helper.py", "old_str": "", "new_str": "X = 1\n"})], "tool_use"),
        DONE, DONE, DONE,                                   # three failed attempts to finish
        ([text("I couldn't fix it, so the changes were reverted.")], "end_turn"),
    ])
    stats = agent.run([{"role": "user", "content": "x"}])
    assert stats["stop"] == "rolled_back" and stats["verify_failures"] == 3
    assert (repo / "calc.py").read_text() == original      # edited file restored
    assert not (repo / "helper.py").exists()                # created file removed
    assert client.requests[-1]["tool_choice"] == {"type": "none"}


def test_preexisting_failures_are_not_blamed_on_agent(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "test_old.py").write_text("def test_already_broken():\n    assert False\n")
    agent, _ = agent_for(repo, [edit(1, "calc.py", "a + b", "b + a"), DONE])
    stats = agent.run([{"role": "user", "content": "x"}])
    assert stats["stop"] == "done" and stats["verify_failures"] == 0


def test_no_edits_means_no_check(tmp_path):
    agent, _ = agent_for(make_repo(tmp_path), [DONE])
    assert agent.run([{"role": "user", "content": "hi"}])["verify_runs"] == 0


def test_verify_can_be_disabled(tmp_path):
    repo = make_repo(tmp_path)
    client = FakeClient([edit(1, "calc.py", "a + b", "a - b"), DONE])
    agent = Agent(workspace=repo, approver=auto_approve, client=client, verbose=False, verify=False)
    stats = agent.run([{"role": "user", "content": "x"}])
    assert stats["stop"] == "done" and stats["verify_runs"] == 0


def test_run_tests_parses_failing_ids(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_ok():\n    pass\n\ndef test_bad():\n    assert 0\n")
    failing, _ = run_tests(tmp_path)
    assert failing == {"test_a.py::test_bad"}


def test_baseline_taken_before_first_write(tmp_path):
    repo = make_repo(tmp_path)
    v = Verifier(repo)
    v.before_write(repo / "calc.py")
    assert v.baseline == set() and v.dirty


def test_eval_classifies_rollback():
    assert ev.classify(False, {"stop": "rolled_back", "edits": 2}) == "rolled_back"
