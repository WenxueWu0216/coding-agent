"""Record and replay browser workflows.

  python browser_cli.py record URL --out flow.json [--name NAME] [--allowlist FILE]
      Opens a browser window. Do the task by hand, then close the window; every click, typed value,
      select and checkbox is saved to flow.json. Values typed into password-like fields are not saved.

  python browser_cli.py replay flow.json [--mode selector|fallback|repair] [--model M] [--start-url URL]
                                         [--value N=ENV_VAR ...] [--trace FILE] [--trace-content]
                                         [--screenshots DIR] [--save-repaired FILE] [--headed]
      Replays the steps. In repair mode a step whose element is gone is matched by the model; you are
      asked before a low-confidence repair or any credential entry is used.

Every URL must pass the allowlist (default config/browser_allowlist.json); see browser_safety.py.
"""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from browser import BrowserSession, ask_credentials
from browser_safety import DEFAULT_CONFIG, BrowserPolicy
import workflow as wf_mod

TRACE_DIR = Path(__file__).resolve().parent / "traces" / "browser"


def record(args):
    if not args.headless and not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        sys.exit("recording needs a visible browser window, but no DISPLAY is set")
    session = BrowserSession(BrowserPolicy.load(args.allowlist), credential_approver=ask_credentials,
                             headless=args.headless)
    try:
        rec = wf_mod.Recorder(session, name=args.name or Path(args.out).stem).start(args.url)
        print(f"Recording {rec.start_url}. Do the task in the browser window, then close it.")
        session.page.wait_for_event("close", timeout=0)
        flow = rec.workflow() if session.page and not session.page.is_closed() else {
            "version": 1, "name": rec.name, "start_url": rec.start_url, "recorded_at": wf_mod.utc_now(),
            "steps": rec.steps}
    except KeyboardInterrupt:
        flow = {"version": 1, "name": rec.name, "start_url": rec.start_url, "recorded_at": wf_mod.utc_now(),
                "steps": rec.steps}
    finally:
        session.close()
    wf_mod.save(flow, args.out)
    print(f"saved {len(flow['steps'])} steps to {args.out}")


def replay(args):
    flow = wf_mod.load(args.workflow)
    values = {}
    for spec in args.value or []:
        n, _, var = spec.partition("=")
        if not n.isdigit() or var not in os.environ:
            sys.exit(f"--value {spec!r}: expected STEP=ENV_VAR with ENV_VAR set")
        values[int(n)] = os.environ[var]
    trace_path = Path(args.trace) if args.trace else TRACE_DIR / f"{Path(args.workflow).stem}-{uuid.uuid4().hex[:8]}.jsonl"
    client = None
    if args.mode == "repair":
        from llm_client import make_client
        client = make_client()
    result = wf_mod.traced_replay(flow, BrowserPolicy.load(args.allowlist), args.mode, trace_path,
                                  run_id=f"replay-{uuid.uuid4().hex[:8]}", client=client, model=args.model,
                                  screenshot_dir=args.screenshots, trace_content=args.trace_content,
                                  start_url=args.start_url, headless=not args.headed,
                                  credential_approver=ask_credentials, repair_approver=wf_mod.ask_repair,
                                  values=values)
    repaired = result.pop("repaired_workflow")
    if args.save_repaired and repaired:
        wf_mod.save(repaired, args.save_repaired)
        print(f"repaired workflow saved to {args.save_repaired}")
    print(json.dumps(result, indent=1))
    print(f"trace: {trace_path}\nnext: python report.py --trace-file {trace_path}")
    sys.exit(0 if result["passed"] else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("url")
    r.add_argument("--out", required=True)
    r.add_argument("--name")
    r.add_argument("--allowlist", type=Path, default=DEFAULT_CONFIG)
    r.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("replay")
    p.add_argument("workflow")
    p.add_argument("--mode", choices=list(wf_mod.MODES), default="repair")
    p.add_argument("--model", default=wf_mod.REPAIR_MODEL)
    p.add_argument("--start-url", help="replay against another origin (e.g. staging)")
    p.add_argument("--value", action="append", help="STEP=ENV_VAR: value for a step recorded without one")
    p.add_argument("--allowlist", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--trace")
    p.add_argument("--trace-content", action="store_true")
    p.add_argument("--screenshots", type=Path)
    p.add_argument("--save-repaired")
    p.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    (record if args.cmd == "record" else replay)(args)


if __name__ == "__main__":
    main()
