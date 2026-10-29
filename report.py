"""Inspect eval runs.

  python report.py latest                 summary of the newest run
  python report.py RUN_ID                 summary of one run
  python report.py OLD_RUN NEW_RUN        regression report between two runs
  python report.py RUN_ID --trace TASK    step-by-step trace of one task
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent / "evals" / "runs"


def resolve(run):
    if run == "latest":
        runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())
        if not runs:
            sys.exit("no runs yet")
        return runs[-1]
    p = RUNS_DIR / run
    if not p.exists():
        sys.exit(f"run not found: {p}")
    return p


def load(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text())
    rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    return manifest, rows


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0


def pass_rate(rows):
    return sum(r["passed"] for r in rows) / len(rows) if rows else 0


def summary(run_dir):
    m, rows = load(run_dir)
    print(f"run       {m['run_id']}")
    print(f"model     {m['model']}   prompt={m['prompt_version']}  tools={m['tools_version']}  "
          f"tasks={m['taskset_version']}  git={m['git']['commit']}{' (dirty)' if m['git']['dirty'] else ''}")
    print(f"result    {sum(r['passed'] for r in rows)}/{len(rows)} passed ({pass_rate(rows):.0%})   "
          f"cost ${sum(r['cost_usd'] for r in rows):.4f}")

    for key in ("suite", "category"):
        print(f"\nby {key}")
        groups = defaultdict(list)
        for row in rows:
            groups[row.get(key, "core")].append(row)
        for name, rs in sorted(groups.items()):
            print(f"  {name:<14} {pass_rate(rs):>5.0%}  (n={len(rs)})")

    print("\nfailure taxonomy (per task run)")
    for kind, n in Counter(r["failure_type"] for r in rows).most_common():
        print(f"  {kind:<14} {n}")

    kinds = Counter()
    for r in rows:
        kinds.update(r.get("error_kinds", {}))
    if kinds:
        print("\ntool errors (agent recovered from some of these)")
        for kind, n in kinds.most_common():
            print(f"  {kind:<14} {n}")

    vr = sum(r.get("verify_runs", 0) for r in rows)
    if vr:
        vf = sum(r.get("verify_failures", 0) for r in rows)
        caught = sum(1 for r in rows if r.get("verify_failures") and r["passed"])
        print(f"\ncompletion gate")
        print(f"  checks run {vr}   failed {vf}   task runs that failed a check and still passed: {caught}")

    wall = [r["wall_ms"] for r in rows]
    model_ms = sum(r["model_ms"] for r in rows)
    tool_ms = sum(r["tool_ms"] for r in rows)
    total = model_ms + tool_ms or 1
    print("\nlatency")
    print(f"  per task  p50 {pct(wall, .5) / 1000:.1f}s   p95 {pct(wall, .95) / 1000:.1f}s")
    print(f"  split     model {model_ms / total:.0%}   tools {tool_ms / total:.0%}")

    tin = sum(r["input_tokens"] for r in rows)
    tout = sum(r["output_tokens"] for r in rows)
    print("\ntokens")
    print(f"  input {tin:,}   output {tout:,}   input share {tin / ((tin + tout) or 1):.0%}")
    print(f"  per task  mean {statistics.mean(r['input_tokens'] + r['output_tokens'] for r in rows):,.0f}")

    flaky = [t for t, rs in group_by_task(rows).items() if 0 < sum(r["passed"] for r in rs) < len(rs)]
    if flaky:
        print(f"\nflaky (passed some repeats, not all): {', '.join(flaky)}")
    fails = sorted({r["task"] for r in rows if not r["passed"]})
    if fails:
        print(f"\ninspect a failure: python report.py {m['run_id']} --trace {fails[0]}")


def group_by_task(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["task"]].append(r)
    return g


def compare(old_dir, new_dir, threshold):
    m1, r1 = load(old_dir)
    m2, r2 = load(new_dir)
    print(f"old  {m1['run_id']}\nnew  {m2['run_id']}\n")
    for key in ("model", "prompt_version", "tools_version", "taskset_version"):
        if m1[key] != m2[key]:
            print(f"changed  {key}: {m1[key]} -> {m2[key]}")
    if m1["config"] != m2["config"]:
        print(f"changed  config: {m1['config']} -> {m2['config']}")
    if m1["taskset_version"] != m2["taskset_version"]:
        print("warning: task set changed, so pass rates are not directly comparable")

    p1, p2 = pass_rate(r1), pass_rate(r2)
    c1 = sum(r["cost_usd"] for r in r1) / len(r1)
    c2 = sum(r["cost_usd"] for r in r2) / len(r2)
    print(f"\npass rate   {p1:.0%} -> {p2:.0%}  ({(p2 - p1) * 100:+.0f} pts)")
    print(f"cost/task   ${c1:.4f} -> ${c2:.4f}  ({(c2 / c1 - 1) * 100 if c1 else 0:+.0f}%)")

    g1, g2 = group_by_task(r1), group_by_task(r2)
    regressions, fixes = [], []
    for task in sorted(set(g1) & set(g2)):
        a, b = pass_rate(g1[task]), pass_rate(g2[task])
        if b < a:
            regressions.append(f"{task} ({a:.0%} -> {b:.0%})")
        elif b > a:
            fixes.append(f"{task} ({a:.0%} -> {b:.0%})")
    print(f"\nregressions ({len(regressions)}): {', '.join(regressions) or 'none'}")
    print(f"fixes       ({len(fixes)}): {', '.join(fixes) or 'none'}")

    if p2 < threshold:
        print(f"\nFAIL: pass rate {p2:.0%} is below the {threshold:.0%} threshold")
        return 1
    return 0


def content(e, key, n):
    """Event content, or a placeholder when the trace was written without --trace-content."""
    if key in e:
        v = e[key]
        return (v if isinstance(v, str) else json.dumps(v))[:n]
    if f"{key}_hash" in e:
        return f"[redacted: {e[f'{key}_size_bytes']} bytes, sha {e[f'{key}_hash']}]"
    return ""


def show_trace(run_dir, task):
    files = sorted((run_dir / "traces").glob(f"*{task}*__r*.jsonl"))
    if not files:
        sys.exit(f"no trace for {task}")
    for f in files:
        print(f"===== {f.name}")
        for line in f.read_text().splitlines():
            e = json.loads(line)
            t = f"{e['t_ms'] / 1000:>6.1f}s"
            kind = e["type"]
            if kind in ("task_start", "run_start"):
                print(f"{t}  TASK   {content(e, 'prompt', 300)}")
            elif kind == "model_call":
                print(f"{t}  MODEL  step {e['step']}  {e['latency_ms']}ms  in={e['input_tokens']} "
                      f"out={e['output_tokens']}  stop={e['stop_reason']}")
                says = content(e, "text", 200)
                if says and e.get("text_size_bytes", 1):
                    print(f"         says: {says}")
            elif kind == "tool_call":
                tool = e.get("tool_name") or e.get("tool")
                args = content(e, "args", 150) or f"[redacted: {e.get('args_size_bytes')} bytes, " \
                                                  f"sha {e.get('tool_args_hash')}]"
                flag = f"  ERROR[{e['error']}]" if e.get("error") else ""
                print(f"{t}  TOOL   {tool}({args}){flag}")
                if "output" in e:   # traces written before tool_result existed
                    print(f"         -> {e['output'][:200]!r}")
            elif kind == "tool_result":
                flag = f"ERROR[{e['error']}] " if e.get("error") else ""
                print(f"         -> {flag}{e['latency_ms']}ms  {content(e, 'output', 200)!r}")
            elif kind == "approval_decision":
                print(f"         approval: {e['decision']} ({e.get('approver', '?')})")
            elif kind == "verify":
                status = "ok" if e["ok"] else f"NEW FAILURES {e['new_failures']}"
                print(f"{t}  VERIFY {e['latency_ms']}ms  {status}")
            elif kind == "rollback":
                print(f"{t}  ROLLBACK {content(e, 'files', 200)}")
            elif kind == "api_error":
                print(f"{t}  API    {e['error']}: {content(e, 'message', 300)}")
            elif kind in ("task_end", "run_end"):
                cause = f"  root_cause={e['failure_root_cause']}" if e.get("failure_root_cause") else ""
                print(f"{t}  END    passed={e['passed']}  {e['failure_type']}{cause}  ${e['cost_usd']:.4f}")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="run id(s), or 'latest'")
    ap.add_argument("--trace", help="print the trace of one task")
    ap.add_argument("--threshold", type=float, default=0.0, help="fail (exit 1) if new pass rate is below this")
    args = ap.parse_args()

    dirs = [resolve(r) for r in args.runs]
    if args.trace:
        show_trace(dirs[0], args.trace)
    elif len(dirs) == 1:
        summary(dirs[0])
    else:
        sys.exit(compare(dirs[0], dirs[1], args.threshold))


if __name__ == "__main__":
    main()
