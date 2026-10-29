"""Tests for hunk-level diff review."""
import io
from types import SimpleNamespace as NS

from agent import Agent, auto_approve
from review import apply_hunks, interactive_review, make_hunks

OLD = "".join(f"line{i}\n" for i in range(1, 21))
NEW = OLD.replace("line2\n", "LINE2\n").replace("line18\n", "LINE18\n")   # two far-apart changes


def answers(*keys):
    it = iter(keys)
    return lambda prompt: next(it)


def test_far_apart_changes_make_two_hunks():
    hunks = make_hunks(OLD, NEW)
    assert len(hunks) == 2
    assert hunks[0].lines[0].startswith("@@ -1,")
    assert "-line2" in hunks[0].lines and "+LINE2" in hunks[0].lines


def test_apply_mixed_decisions():
    hunks = make_hunks(OLD, NEW)
    assert apply_hunks(OLD, NEW, hunks, ["y", "y"]) == NEW
    assert apply_hunks(OLD, NEW, hunks, ["n", "n"]) == OLD
    partial = apply_hunks(OLD, NEW, hunks, ["y", "n"])
    assert "LINE2" in partial and "line18" in partial and "LINE18" not in partial


def test_user_edit_replaces_hunk():
    r = interactive_review("f.py", OLD, NEW, ask=answers("e", "y"), out=io.StringIO(),
                           editor=lambda lines: ["Line2-by-user\n"])
    assert "Line2-by-user" in r.text and "LINE18" in r.text
    assert (r.accepted, r.rejected, r.edited) == (1, 0, 1)


def test_accept_rest_and_help():
    r = interactive_review("f.py", OLD, NEW, ask=answers("?", "a"), out=io.StringIO())
    assert r.text == NEW and r.accepted == 2


def test_new_file_is_one_hunk():
    r = interactive_review("new.py", "", "a = 1\nb = 2\n", ask=answers("y"), out=io.StringIO())
    assert r.text == "a = 1\nb = 2\n" and r.total == 1


class Client:
    def __init__(self, script):
        self.script, self.messages = list(script), self

    def create(self, **kw):
        content, stop = self.script.pop(0)
        return NS(content=content, stop_reason=stop, usage=NS(input_tokens=1, output_tokens=1))


def run_edit(tmp_path, keys):
    (tmp_path / "f.py").write_text(OLD)
    call = NS(type="tool_use", id="t1", name="edit_file",
              input={"path": "f.py", "old_str": OLD, "new_str": NEW})
    reviewer = lambda path, old, new: interactive_review(path, old, new, ask=answers(*keys), out=io.StringIO())
    agent = Agent(workspace=tmp_path, approver=auto_approve, client=Client([]), verbose=False,
                  verify=False, reviewer=reviewer)
    return agent.run_tool(call.name, call.input)


def test_partial_accept_tells_the_model(tmp_path):
    output, err = run_edit(tmp_path, ["y", "n"])
    assert err is None and "1 accepted, 1 rejected" in output and "NOT applied" in output
    text = (tmp_path / "f.py").read_text()
    assert "LINE2" in text and "LINE18" not in text


def test_reject_all_writes_nothing(tmp_path):
    output, err = run_edit(tmp_path, ["d"])
    assert err == "denied" and (tmp_path / "f.py").read_text() == OLD


def test_reviewer_replaces_generic_approval(tmp_path):
    asked = []
    (tmp_path / "f.py").write_text(OLD)
    agent = Agent(workspace=tmp_path, approver=lambda n, a: asked.append(n) or True, client=Client([]),
                  verbose=False, verify=False,
                  reviewer=lambda p, o, n: interactive_review(p, o, n, ask=answers("a"), out=io.StringIO()))
    agent.run_tool("edit_file", {"path": "f.py", "old_str": "line2\n", "new_str": "x\n"})
    assert asked == []
