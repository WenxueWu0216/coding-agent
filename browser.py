"""Playwright browser session behind the browser tools, the MCP browser server and workflow replay.

Every action goes through BrowserSession.perform(), which resolves the target element, runs the
action, takes a screenshot (always on failure; on every step if screenshot_every_step) and writes
one browser_step trace event saying what was tried, what matched, what happened and how long it
took. All network requests are checked against the BrowserPolicy (browser_safety.py).

Targets are dicts. An agent usually passes {"ref": "e7"} from the last browser_read_page view;
recorded workflows carry {"selector", "role", "name", "label", "text", "placeholder"}, tried in
the order given by `strategies` (see workflow.py for the replay modes).
"""
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from browser_safety import BrowserPolicy, BrowserPolicyError, looks_secret, untrusted
from tracing import null_trace, scrub

DOM_JS = (Path(__file__).resolve().parent / "browser_dom.js").read_text()
DESCRIBE = f"(el) => {{\n{DOM_JS}\nreturn A.describe(el);\n}}"
SNAPSHOT = f"(arg) => {{\n{DOM_JS}\nreturn A.snapshot(arg.attr, arg.max);\n}}"
NEARBY = f"""(arg) => {{
{DOM_JS}
const els = Array.from(document.querySelectorAll('a[href], button, input, select, textarea, [role]'))
  .filter(e => e.type !== 'hidden' && (!e.checkVisibility || e.checkVisibility()));
const want = arg.role;
const pick = els.filter(e => !want || A.role(e) === want);
return (pick.length ? pick : els).slice(0, 15).map(e => {{
  const d = A.describe(e); return `${{d.role || d.tag}} ${{JSON.stringify(d.name)}} (${{d.selector}})`; }});
}}"""

TARGET_KEYS = ("ref", "selector", "role", "name", "label", "text", "placeholder")
STRATEGIES = ("ref", "selector", "role", "label", "text", "placeholder")   # most to least specific
ELEMENT_ACTIONS = ("click", "type", "select", "check", "press")


class BrowserError(Exception):
    """An expected browser failure. `kind` goes into the trace and the agent's error taxonomy."""

    def __init__(self, kind: str, message: str, **details):
        super().__init__(message)
        self.kind = kind
        self.details = details


class CredentialDenied(BrowserError):
    def __init__(self, message: str):
        super().__init__("denied", message)


def deny_credentials(request: dict) -> bool:
    """Default credential gate: no human is present, so the answer is no."""
    return False


def ask_credentials(request: dict) -> bool:
    """Interactive credential gate for the CLI."""
    print(f"\n  The agent wants to type into a sensitive field ({request['reason']}): "
          f"{request['field']!r} on {request['url']}")
    return input("  Allow? [y/N] ").strip().lower() == "y"


def same_text(a: Optional[str], b: Optional[str]) -> bool:
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def describe_target(target: Optional[dict]) -> str:
    if not target:
        return "(no target)"
    if target.get("ref"):
        return f"ref {target['ref']}"
    if target.get("role") and target.get("name"):
        return f"{target['role']} {target['name']!r}"
    for k in ("label", "text", "placeholder", "selector", "name"):
        if target.get(k):
            return f"{k} {target[k]!r}"
    return str(target)


class BrowserSession:
    def __init__(self, policy: BrowserPolicy, credential_approver: Callable[[dict], bool] = deny_credentials,
                 trace=null_trace, screenshot_dir: Optional[Path] = None, screenshot_every_step: bool = False,
                 headless: bool = True, wait_ms: int = 2000, action_timeout_ms: int = 5000,
                 nav_timeout_ms: int = 15000):
        self.policy = policy
        self.credential_approver = credential_approver
        self.trace = trace
        self.parent: Optional[str] = None   # trace parent for the next events (the agent's tool_call)
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self.screenshot_every_step = screenshot_every_step
        self.headless = headless
        self.wait_ms = wait_ms
        self.action_timeout_ms = action_timeout_ms
        self.nav_timeout_ms = nav_timeout_ms
        self.ref_attr = f"data-ar-{uuid.uuid4().hex[:8]}"   # random, so a page can't pre-plant refs
        self.blocked: list[dict] = []   # requests refused by the policy
        self.step_no = 0
        self.page = None
        self._pw = self._browser = self.context = None

    # ---------- lifecycle ----------
    def start(self):
        if self.page:
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self.context = self._browser.new_context(service_workers="block", accept_downloads=False,
                                                 viewport={"width": 1280, "height": 800})
        self.context.route("**/*", self._guard)
        self.context.route_web_socket("**/*", self._guard_ws)
        self.page = self.context.new_page()

    def close(self):
        for closer in (getattr(self.context, "close", None), getattr(self._browser, "close", None),
                       getattr(self._pw, "stop", None)):
            if closer:
                try:
                    closer()
                except Exception:
                    pass
        self.page = self.context = self._browser = self._pw = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _guard(self, route):
        """Checks every request. Playwright routes do not see redirect hops, so requests are fetched
        here with redirects off; a 3xx is passed to the browser only if its Location is allowed too,
        and the browser's follow-up request comes back through this guard."""
        url = route.request.url
        try:
            self.policy.check(url)
        except BrowserPolicyError as e:
            self._block(route, url, e)
            return
        try:
            response = route.fetch(max_redirects=0, timeout=self.nav_timeout_ms)
        except PlaywrightError:
            route.abort("failed")
            return
        if 300 <= response.status < 400 and response.headers.get("location"):
            target = urljoin(url, response.headers["location"])
            try:
                self.policy.check(target)
            except BrowserPolicyError as e:
                self._block(route, target, e, via=url)
                return
        route.fulfill(response=response)

    def _block(self, route, url, error, via=None):
        self.blocked.append({"url": url[:200], "reason": str(error), **({"redirected_from": via[:200]} if via else {})})
        route.abort("blockedbyclient")

    def _guard_ws(self, ws):
        """A blocked websocket is never connected to its server: the page gets a mock socket that
        carries nothing. (Calling ws.close() from a sync handler deadlocks Playwright 1.63.)"""
        try:
            self.policy.check(ws.url.replace("ws://", "http://", 1).replace("wss://", "https://", 1))
        except BrowserPolicyError as e:
            self.blocked.append({"url": ws.url[:200], "reason": str(e)})
            return
        ws.connect_to_server()

    def _need_page(self):
        if not self.page:
            raise BrowserError("no_page", "no page is open; call browser_open first")

    # ---------- one step ----------
    def perform(self, action: str, target: Optional[dict] = None, value=None, strategies=STRATEGIES,
                validate: bool = False, repairer=None, step: Optional[int] = None, source: str = "agent",
                max_chars: int = 6000, full_page: bool = False):
        """Run one action and trace it. Returns the result text; raises BrowserError/BrowserPolicyError."""
        self.step_no += 1
        n = step or self.step_no
        t0 = time.time()
        blocked_before = len(self.blocked)
        info: dict = {"attempts": [], "resolved_by": None, "element": None, "repair": None}
        error, result, shot = None, None, None
        try:
            if action == "open":
                result = self._open(value)
            elif action == "read_page":
                result = self.read_page(max_chars)
            elif action == "screenshot":
                self._need_page()
                shot = self._shot(n, "screenshot", full_page=full_page)
                result = f"Saved screenshot to {shot}"
            elif action in ELEMENT_ACTIONS:
                self._need_page()
                loc, how = self._resolve(target or {}, strategies, info["attempts"], validate)
                if loc is None and repairer:
                    loc, how, info["repair"] = repairer(self, action, target or {}, value, info["attempts"], n)
                if loc is None:
                    raise BrowserError("selector_not_found", self._not_found(target, info["attempts"]),
                                       nearby=self._nearby(target))
                info["resolved_by"] = how
                info["element"] = loc.evaluate(DESCRIBE)
                result = self._act(action, loc, value, info["element"])
            else:
                raise BrowserError("bad_args", f"unknown browser action {action!r}")
        except (BrowserError, BrowserPolicyError) as e:
            error = e
        except PlaywrightTimeout as e:
            error = BrowserError("timeout", str(e).splitlines()[0][:300])
        except PlaywrightError as e:
            error = BrowserError("action_failed", str(e).splitlines()[0][:300])
        ms = round((time.time() - t0) * 1000)

        if self.page and not shot and (error or self.screenshot_every_step):
            shot = self._shot(n, "error" if error else action)
        sensitive = bool(info["element"] and info["element"].get("sensitive")) or looks_secret(str(value or ""))
        kind = getattr(error, "kind", "policy_block" if isinstance(error, BrowserPolicyError) else None)
        fields = dict(
            step=n, tool_name=f"browser_{action}", action=action, source=source,
            target={k: target[k] for k in TARGET_KEYS if target and target.get(k)} or None,
            strategies=list(strategies) if action in ELEMENT_ACTIONS else None,
            result="error" if error else "ok", status="error" if error else "ok", error=kind,
            error_detail=scrub(str(error))[:500] if error else None, latency_ms=ms,
            url=self.page.url if self.page else None, screenshot_path=str(shot) if shot else None,
            resolved_by=info["resolved_by"], attempts=info["attempts"] or None,
            element={k: v for k, v in (info["element"] or {}).items() if v not in (None, False)} or None,
            repaired=info["repair"] is not None, repair_event_id=(info["repair"] or {}).get("event_id"),
            blocked_requests=self.blocked[blocked_before:] or None,
            nearby=scrub(error.details.get("nearby")) if isinstance(error, BrowserError) and error.details else None,
        )
        if action == "type":
            if sensitive:
                fields["value_redacted"] = True   # never stored, not even with --trace-content
            elif value is not None:
                fields["value"] = str(value)      # hashed unless trace_content
        elif action in ("select", "check", "press", "open") and value is not None:
            fields["value"] = str(value)
        self.trace("browser_step", parent=self.parent, **{k: v for k, v in fields.items() if v is not None})
        if error:
            raise error
        return result

    # ---------- actions ----------
    def _open(self, url):
        if not isinstance(url, str) or not url:
            raise BrowserError("bad_args", "browser_open needs a url")
        self.policy.check(url)
        self.start()
        try:
            response = self.page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
        except PlaywrightError as e:
            if "ERR_BLOCKED_BY_CLIENT" in str(e) and self.blocked:
                raise BrowserPolicyError(f"navigation blocked: {self.blocked[-1]['reason']}") from None
            raise
        if not self.policy.allows(self.page.url):   # e.g. a redirect the route guard did not see
            final = self.page.url
            self.page.goto("about:blank")
            raise BrowserPolicyError(f"page ended up at a blocked URL ({final[:100]}); navigation undone")
        status = response.status if response else "?"
        return f"Opened {self.page.url} (HTTP {status}).\n" + self.read_page()

    def read_page(self, max_chars: int = 6000) -> str:
        self._need_page()
        snap = self.page.evaluate(SNAPSHOT, {"attr": self.ref_attr, "max": max(500, min(int(max_chars), 20000))})
        body = f"title: {snap['title']}\n{snap['view']}" if snap["view"] else f"title: {snap['title']}\n(no visible content)"
        return untrusted(body, self.page.url) + ("\nRefs like [e3] are valid until the page changes; "
                                                 "read the page again after actions that change it.")

    def _act(self, action, loc, value, el):
        label = f"{el.get('role') or el.get('tag')} {el.get('name')!r}"
        t = self.action_timeout_ms
        if action == "click":
            loc.click(timeout=t)
            self._settle()
            return f"Clicked {label}. Page is now {self.page.url}."
        if value is None:
            raise BrowserError("missing_value", f"{action} needs a value")
        if action == "type":
            if el.get("sensitive") or looks_secret(str(value)):
                self._credential_gate(el, "sensitive field" if el.get("sensitive") else "value looks like a secret")
            loc.fill(str(value), timeout=t)
            return f"Typed {len(str(value))} characters into {label}."
        if action == "select":
            try:
                loc.select_option(label=str(value), timeout=t)
            except PlaywrightError:
                loc.select_option(value=str(value), timeout=t)
            return f"Selected {value!r} in {label}."
        if action == "check":
            on = value not in (False, "false", "False", "0", "off", "no", 0)
            loc.set_checked(on, timeout=t)
            return f"{'Checked' if on else 'Unchecked'} {label}."
        loc.press(str(value), timeout=t)
        self._settle()
        return f"Pressed {value} in {label}."

    def _settle(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=self.nav_timeout_ms)
        except PlaywrightError:
            pass

    def _credential_gate(self, el, reason):
        request = {"url": self.page.url, "field": el.get("name") or el.get("label") or el.get("selector"),
                   "selector": el.get("selector"), "reason": reason}
        approved = self.credential_approver(request) is True
        self.trace("approval_decision", parent=self.parent, tool_name="browser_type",
                   decision="approved" if approved else "denied",
                   approver=getattr(self.credential_approver, "__name__", "unknown"), gate="credential",
                   field=request["field"], reason=reason)
        if not approved:
            raise CredentialDenied(f"typing into {request['field']!r} ({reason}) needs human approval, and it "
                                   f"was not given. Nothing was typed. Do not retry; ask the user.")

    # ---------- element resolution ----------
    def _locator(self, strategy, target):
        p = self.page
        if strategy == "ref" and target.get("ref"):
            return p.locator(f'[{self.ref_attr}="{target["ref"]}"]'), target["ref"]
        if strategy == "selector" and target.get("selector"):
            return p.locator(target["selector"]), target["selector"]
        if strategy == "role" and target.get("role") and target.get("name"):
            return p.get_by_role(target["role"], name=target["name"], exact=True), f"{target['role']} {target['name']!r}"
        if strategy == "label" and target.get("label"):
            return p.get_by_label(target["label"], exact=True), target["label"]
        if strategy == "text" and target.get("text"):
            return p.get_by_text(target["text"], exact=True), target["text"]
        if strategy == "placeholder" and target.get("placeholder"):
            return p.get_by_placeholder(target["placeholder"], exact=True), target["placeholder"]
        return None, None

    def _resolve(self, target, strategies, attempts, validate):
        """First strategy that matches exactly one visible element. With validate, a selector match
        whose role or name differs from the recorded one is treated as stale and skipped, so a moved
        element is not silently swapped for whatever now sits at that position. Polls up to wait_ms."""
        deadline = time.time() + self.wait_ms / 1000
        while True:
            attempts.clear()
            for s in strategies:
                loc, query = self._locator(s, target)
                if loc is None:
                    continue
                rec = {"strategy": s, "query": str(query)[:120]}
                attempts.append(rec)
                try:
                    loc = loc.filter(visible=True)
                    rec["matches"] = loc.count()
                except PlaywrightError as e:
                    rec.update(matches=None, result="invalid", detail=str(e).splitlines()[0][:120])
                    continue
                if rec["matches"] != 1:
                    rec["result"] = "not_found" if rec["matches"] == 0 else "ambiguous"
                    continue
                if validate and s == "selector":
                    found = loc.evaluate(DESCRIBE)
                    stale = [k for k in ("role", "name") if target.get(k) and found.get(k)
                             and not same_text(target[k], found[k])]
                    if stale:
                        rec.update(result="mismatch", found={k: found.get(k) for k in ("role", "name")})
                        continue
                rec["result"] = "ok"
                return loc, s
            if time.time() >= deadline:
                return None, None
            time.sleep(0.1)

    def _not_found(self, target, attempts):
        tried = "; ".join(f"{a['strategy']} {a['query']!r} -> {a.get('result')}"
                          + (f" ({a['matches']})" if a.get("matches") else "") for a in attempts)
        hint = " Refs expire when the page changes: call browser_read_page and use a fresh ref." \
            if target and target.get("ref") else ""
        return f"no unique visible element for {describe_target(target)}. Tried: {tried or 'nothing usable'}.{hint}"

    def _nearby(self, target):
        try:
            return self.page.evaluate(NEARBY, {"role": (target or {}).get("role")})
        except PlaywrightError:
            return None

    def _shot(self, n, label, full_page=False):
        if not self.screenshot_dir:
            self.screenshot_dir = Path(tempfile.mkdtemp(prefix="browser-shots-"))
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"step{n:03d}-{label}.png"
        try:
            self.page.screenshot(path=str(path), full_page=full_page, timeout=self.action_timeout_ms)
            return path
        except PlaywrightError:
            return None

    # ---------- conveniences for the tools ----------
    def open(self, url):
        return self.perform("open", value=url)

    def click(self, target):
        return self.perform("click", target)

    def type(self, target, value):
        return self.perform("type", target, value)

    def screenshot(self, full_page=False):
        return self.perform("screenshot", full_page=full_page)
