"""Multi-session memory tasks in the eval harness, with a fake model that only knows what is in its prompt."""
import json
import re
from types import SimpleNamespace as NS

import eval as ev


def text(t):
    return NS(type="text", text=t)


def tool(name, inp, i=1):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


class PromptOnlyModel:
    """Answers from the system prompt and the current message alone, like a model with no hidden state.
    Proposes the user's statements as memories; fills in config values only if memory supplied them."""

    def __init__(self):
        self.messages = self
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        usage = NS(input_tokens=100, output_tokens=10)
        if kw["system"].startswith("You maintain the long-term memory"):
            said = re.findall(r"USER: (.+)", kw["messages"][0]["content"])[-1]
            key = "staging_port" if "staging" in said else "vat_rate" if "VAT" in said else None
            reply = {"memories": [{"content": said, "scope": "project", "key": key, "importance": 4}]}
            return NS(content=[text(json.dumps(reply))], stop_reason="end_turn", usage=usage)
        request = kw["messages"][0]["content"]
        if len(kw["messages"]) > 1 or not request.startswith(("Implement", "Set")):
            return NS(content=[text("Done.")], stop_reason="end_turn", usage=usage)
        system = kw["system"]
        if "billing.py" in request:
            rate = re.search(r"VAT rate is (\d+)%", system)
            guess = int(rate.group(1)) / 100 if rate else 0.2   # without memory, a plausible guess
            return NS(content=[
                tool("edit_file", {"path": "billing.py", "old_str": "VAT_RATE = None", "new_str": f"VAT_RATE = {guess}"}),
                tool("edit_file", {"path": "billing.py", "old_str": "raise NotImplementedError",
                                   "new_str": "return round(net * (1 + VAT_RATE), 2)"}, 2),
            ], stop_reason="tool_use", usage=usage)
        ports = re.findall(r"port (\d{4})", system)
        new = f"STAGING_PORT = {ports[0] if ports else None}"
        return NS(content=[tool("edit_file", {"path": "config.py", "old_str": "STAGING_PORT = None", "new_str": new})],
                  stop_reason="tool_use", usage=usage)


def task(tid):
    return ev.load_tasks([tid], suite="memory")[0]


def run(tid, memory):
    model = PromptOnlyModel()
    return ev.run_task(task(tid), "claude-haiku-5-5", 10, client=model, memory=memory, verify=False), model


def test_fact_from_session_one_passes_only_with_memory():
    on, model = run("33_mem_fact", memory=True)
    off, _ = run("33_mem_fact", memory=False)
    assert on["memory"] is True and off["memory"] is False
    loop_systems = [r["system"] for r in model.requests if "tools" in r]
    assert "VAT rate is 21%" not in loop_systems[0] and "VAT rate is 21%" in loop_systems[-1]
    assert on["passed"], on["test_log"]
    assert not off["passed"] and off["edits"] == 2   # it edited, but with a guessed rate
    assert on["memory_tokens"] > 0 and "memory_tokens" not in off


def test_stale_fact_is_replaced_not_duplicated():
    r, model = run("35_mem_stale_fact", memory=True)
    last = [x["system"] for x in model.requests if "tools" in x][-1]
    assert "9091" in last and "port 8080." not in last   # session 2 overwrote the session 1 memory by key
    assert r["passed"], r["test_log"]
    off, _ = run("35_mem_stale_fact", memory=False)
    assert not off["passed"]


def test_memory_suite_is_separate_from_all():
    assert not any(t["suite"] == "memory" for t in ev.load_tasks())
    tasks = ev.load_tasks(suite="memory")
    assert len(tasks) == 5 and all(len(t["sessions"]) >= 2 for t in tasks)


def test_merge_stats_adds_sessions():
    a = {"steps": 2, "tool_calls": 1, "error_kinds": {"denied": 1}, "stop": "done", "verified": False}
    b = {"steps": 3, "tool_calls": 2, "error_kinds": {"denied": 1, "timeout": 1}, "stop": "step_limit",
         "verified": True}
    total = ev.merge_stats(ev.merge_stats(None, a), b)
    assert total == {"steps": 5, "tool_calls": 3, "error_kinds": {"denied": 2, "timeout": 1}, "stop": "step_limit",
                     "verified": True}
    assert a["error_kinds"] == {"denied": 1}   # inputs are not mutated
