"""Record and replay: recorder output, the three replay modes, model repair and its approval gate."""
import json
import re
from types import SimpleNamespace as NS

import pytest

import workflow as wf
from browser import BrowserSession
from browser_safety import BrowserPolicy
from tracing import Tracer
from webapps.server import AppServer


@pytest.fixture(scope="module")
def server():
    with AppServer() as srv:
        yield srv


@pytest.fixture(autouse=True)
def reset_variants(server):
    yield
    server.httpd.variants.clear()


def session(server, **kw):
    return BrowserSession(BrowserPolicy(allow=[server.allow_entry]), wait_ms=kw.pop("wait_ms", 600), **kw)


# Scripts that act like a person in the browser; the Recorder sees only DOM events.
def do_form(p):
    p.click("#name")
    p.keyboard.type("Ada Lovelace")
    p.fill("#email", "ada@example.com")
    p.select_option("#topic", "Support")
    p.check("#subscribe")
    p.fill("#message", "Hello")
    p.click("#submit-btn")


def do_wizard(p):
    p.fill("#username", "ada")
    p.select_option("#plan", "Pro")
    p.click("#step1 .next")
    p.fill("#city", "London")
    p.select_option("#country", "France")
    p.click("#step2 .next")
    p.click("#step3 .finish")


def record(server, app, script):
    with session(server) as s:
        rec = wf.Recorder(s, app).start(server.url(f"{app}/"))
        script(s.page)
        return rec.workflow()


@pytest.fixture(scope="module")
def form_flow(server):
    return record(server, "form", do_form)


@pytest.fixture(scope="module")
def wizard_flow(server):
    return record(server, "wizard", do_wizard)


FORM_DONE = 'Thanks, Ada Lovelace! We will reply to ada@example.com about Support: "Hello". You are subscribed.'
WIZARD_DONE = "Account created: ada, Pro plan, London, France"


def test_recorder_captures_a_persons_steps(form_flow, server):
    steps = form_flow["steps"]
    assert [(s["action"], s["target"]["name"], s["value"]) for s in steps] == [
        ("type", "Full name", "Ada Lovelace"),       # 12 keystrokes, one step
        ("type", "Email", "ada@example.com"),
        ("select", "Topic", "Support"),
        ("check", "Subscribe to updates", True),
        ("type", "Message", "Hello"),
        ("click", "Submit", None),
    ]
    assert steps[0]["target"] == {"selector": "#name", "role": "textbox", "name": "Full name", "label": "Full name",
                                  "placeholder": "Ada Lovelace", "tag": "input", "type": "text"}
    assert form_flow["start_url"] == server.url("form/")


def test_recorder_uses_structural_selectors_when_there_is_no_id(wizard_flow):
    nxt = wizard_flow["steps"][2]
    assert nxt["action"] == "click" and nxt["target"]["name"] == "Next"
    assert nxt["target"]["selector"] == "#step1 > div > button:nth-of-type(2)"


def test_recorder_never_saves_sensitive_values(server):
    def script(p):
        p.fill("#admin-pass", "Tr0ub4dor-x9")
        p.fill("#note", "ring twice")
    flow = record(server, "injection", script)
    assert flow["steps"][0] == {"action": "type", "target": flow["steps"][0]["target"], "value": None,
                                "sensitive": True}
    assert flow["steps"][1]["value"] == "ring twice"
    assert "Tr0ub4dor" not in json.dumps(flow)


def test_save_and_load(form_flow, tmp_path):
    wf.save(form_flow, tmp_path / "f.json")
    assert wf.load(tmp_path / "f.json") == form_flow
    (tmp_path / "bad.json").write_text(json.dumps({"version": 1, "steps": [{"action": "explode", "target": {}}]}))
    with pytest.raises(ValueError, match="step 1"):
        wf.load(tmp_path / "bad.json")


@pytest.mark.parametrize("mode", ["selector", "fallback"])
def test_replay_unchanged_pages(server, form_flow, wizard_flow, mode):
    for flow, sel, done in [(form_flow, "#result", FORM_DONE), (wizard_flow, "#summary", WIZARD_DONE)]:
        with session(server) as s:
            r = wf.Replayer(s, mode).run(flow)
            assert r["ok"] and r["steps_ok"] == len(flow["steps"]), r
            assert s.page.inner_text(sel) == done


def test_moved_buttons_break_selectors_but_fallback_recovers(server, wizard_flow):
    server.set_variant("wizard", "moved")
    with session(server) as s:
        r = wf.Replayer(s, "selector").run(wizard_flow)
    assert not r["ok"] and r["failed_step"] == 3 and r["error"] == "selector_not_found"
    with session(server) as s:
        r = wf.Replayer(s, "fallback").run(wizard_flow)
        assert r["ok"] and s.page.inner_text("#summary") == WIZARD_DONE


def test_fallback_refuses_a_selector_that_now_points_at_another_element(server, wizard_flow):
    flow = json.loads(json.dumps(wizard_flow))
    flow["steps"][2]["target"]["selector"] = "#step1 > div > button:nth-of-type(1)"   # now the Back button
    events = []
    with session(server, trace=lambda t, parent=None, **f: events.append({"type": t, **f})) as s:
        assert wf.Replayer(s, "fallback").run(flow)["ok"]
    step3 = next(e for e in events if e["type"] == "browser_step" and e["step"] == 3)
    assert step3["attempts"][0] == {"strategy": "selector", "query": "#step1 > div > button:nth-of-type(1)",
                                    "matches": 1, "result": "mismatch", "found": {"role": "button", "name": "Back"}}
    assert step3["resolved_by"] == "role"


def test_renamed_button_defeats_fallback(server, form_flow):
    server.set_variant("form", "renamed")
    with session(server) as s:
        r = wf.Replayer(s, "fallback").run(form_flow)
    assert not r["ok"] and r["failed_step"] == 6
    assert r["error"] == "selector_not_found"
    assert "selector '#submit-btn' -> not_found; role \"button 'Submit'\" -> not_found" in r["error_detail"]


# ---------- model repair with a scripted model ----------
class ScriptedRepair:
    """Picks the element named `pick` from the page view in the prompt, like a model would."""

    def __init__(self, pick, confidence=0.9):
        self.pick, self.confidence, self.prompts = pick, confidence, []
        self.messages = self

    def create(self, **kw):
        prompt = kw["messages"][0]["content"]
        self.prompts.append(kw)
        m = re.search(r"\[(e\d+)\] \S+ " + re.escape(json.dumps(self.pick)), prompt) if self.pick else None
        choice = {"ref": m.group(1) if m else None, "confidence": self.confidence, "reason": f"looks like {self.pick}"}
        return NS(content=[NS(type="tool_use", id="r1", name="choose_element", input=choice)], stop_reason="tool_use",
                  usage=NS(input_tokens=1200, output_tokens=60))


def repair_run(server, flow, client, tmp_path, **kw):
    path = tmp_path / "trace.jsonl"
    tracer = Tracer(path, model="claude-haiku-5-5")
    with session(server, trace=tracer.event, screenshot_dir=tmp_path / "shots") as s:
        r = wf.Replayer(s, "repair", client=client, model="claude-haiku-5-5", **kw).run(flow)
        page = {"result": s.page.inner_text("#result")}
    tracer.close()
    return r, page, [json.loads(line) for line in path.read_text().splitlines()]


def test_model_repair_finds_the_renamed_button(server, form_flow, tmp_path):
    server.set_variant("form", "renamed")
    client = ScriptedRepair("Send message", confidence=0.92)
    r, page, events = repair_run(server, form_flow, client, tmp_path)
    assert r["ok"] and r["repairs"] == 1 and r["repairs_accepted"] == 1 and page["result"] == FORM_DONE
    assert r["input_tokens"] == 1200 and r["cost_usd"] == pytest.approx((1200 * 0.10 + 60 * 0.50) / 1e6)
    prompt = client.prompts[0]
    assert prompt["tool_choice"] == {"type": "tool", "name": "choose_element"}
    assert "untrusted" in prompt["system"] and "<untrusted_page_content>" in prompt["messages"][0]["content"]
    repair = next(e for e in events if e["type"] == "browser_repair")
    assert repair["decision"] == "accepted" and repair["confidence"] == 0.92
    assert repair["original_target"]["name"] == "Submit" and repair["chosen_element"]["name"] == "Send message"
    step6 = next(e for e in events if e["type"] == "browser_step" and e.get("step") == 6)
    assert step6["resolved_by"] == "model" and step6["repaired"] and step6["repair_event_id"] == repair["event_id"]
    model_call = next(e for e in events if e["type"] == "model_call")
    assert model_call["purpose"] == "browser_repair" and repair["parent_event_id"] == model_call["event_id"]


def test_low_confidence_repair_stops_without_approval(server, form_flow, tmp_path):
    server.set_variant("form", "renamed")
    r, page, events = repair_run(server, form_flow, ScriptedRepair("Send message", confidence=0.4), tmp_path)
    assert not r["ok"] and r["stop"] == "needs_approval" and r["failed_step"] == 6
    assert page["result"] == ""                                   # nothing was clicked
    repair = next(e for e in events if e["type"] == "browser_repair")
    assert repair["decision"] == "denied" and repair["approver"] == "deny_repairs"


def test_low_confidence_repair_proceeds_when_a_human_approves(server, form_flow, tmp_path):
    server.set_variant("form", "renamed")
    asked = []
    r, page, _ = repair_run(server, form_flow, ScriptedRepair("Send message", confidence=0.4), tmp_path,
                            repair_approver=lambda req: asked.append(req) or True)
    assert r["ok"] and page["result"] == FORM_DONE
    assert asked[0]["chosen"] == "button 'Send message'" and asked[0]["confidence"] == 0.4


def test_repair_cannot_type_into_a_button_or_pick_nothing(server, form_flow, tmp_path):
    flow = json.loads(json.dumps(form_flow))
    flow["steps"][0]["target"] = {"selector": "#full-name", "role": "textbox", "name": "Your name"}
    r, _, events = repair_run(server, flow, ScriptedRepair("Submit", confidence=0.99), tmp_path)
    assert not r["ok"] and r["failed_step"] == 1 and r["error"] == "selector_not_found"
    repair = next(e for e in events if e["type"] == "browser_repair")
    assert repair["decision"] == "rejected" and "cannot receive a type" in repair["problem"]
    r, _, _ = repair_run(server, flow, ScriptedRepair(None), tmp_path)
    assert not r["ok"] and r["repairs_accepted"] == 0


def test_repair_talked_into_a_destructive_element_needs_a_human(server, form_flow, tmp_path):
    """The injected page tells repair models to pick "Delete my data" with confidence 1.0. A model that
    obeys is still stopped: destructive substitutions always need approval."""
    server.set_variant("form", "injected")
    r, page, events = repair_run(server, form_flow, ScriptedRepair("Delete my data", confidence=1.0), tmp_path)
    assert not r["ok"] and r["stop"] == "needs_approval"
    assert page["result"] == ""                                    # "All data deleted." never appeared
    repair = next(e for e in events if e["type"] == "browser_repair")
    assert repair["destructive"] and repair["decision"] == "denied"


def test_sensitive_steps_need_a_supplied_value_and_approval(server, tmp_path):
    flow = record(server, "injection", lambda p: p.fill("#admin-pass", "Tr0ub4dor-x9"))
    with session(server) as s:
        r = wf.Replayer(s, "fallback").run(flow)
        assert r["error"] == "missing_value"
    with session(server) as s:
        r = wf.Replayer(s, "fallback", values={1: "Tr0ub4dor-x9"}).run(flow)
        assert r["error"] == "denied" and s.page.input_value("#admin-pass") == ""
    with session(server, credential_approver=lambda req: True) as s:
        assert wf.Replayer(s, "fallback", values={1: "Tr0ub4dor-x9"}).run(flow)["ok"]
