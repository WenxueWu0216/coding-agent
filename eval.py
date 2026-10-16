import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

TASKS_DIR = Path(__file__).resolve().parent / "evals" / "tasks"
PROJECT_DIR = Path(__file__).resolve().parent
RUNS_DIR = PROJECT_DIR / "evals" / "runs"

# USD per million tokens (input, output), from the Claude pricing page
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}


def load_tasks(only=None, suite="all"):
    tasks = []
    for d in sorted(TASKS_DIR.iterdir()):
        if d.is_dir() and (only is None or any(o in d.name for o in only)):
            meta = json.loads((d / "task.json").read_text())
            meta.setdefault("suite", "core")
            meta.setdefault("protected", [])
            if suite in ("all", meta["suite"]):
                meta["dir"] = d
                tasks.append(meta)
    return tasks


def tampered_files(task, workspace):
    """Protected files (e.g. visible tests) the agent was told not to edit, but changed or deleted."""
    bad = []
    for rel in task["protected"]:
        original = (task["dir"] / "repo" / rel).read_bytes()
        current = workspace / rel
        if not current.exists() or current.read_bytes() != original:
            bad.append(rel)
    return bad


def run_hidden_test(task_dir, workspace):
    shutil.copy(task_dir / "hidden_test.py", workspace / "test_hidden.py")
    for cache in workspace.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_hidden.py"],
            cwd=workspace, capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return False, "hidden test timed out"


def validate(tasks):
    """Every task must fail before the fix and pass with the reference fix."""
    ok = True
    for t in tasks:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(t["dir"] / "repo", ws, dirs_exist_ok=True)
            before, _ = run_hidden_test(t["dir"], ws)
            shutil.copytree(t["dir"] / "solution", ws, dirs_exist_ok=True)
            after, log = run_hidden_test(t["dir"], ws)
            touches_protected = bool(tampered_files(t, ws))
        good = (not before) and after and not touches_protected
        ok &= good
        print(f"{'OK ' if good else 'BAD'} {t['id']:<24} fails_before={not before} passes_after={after}"
              + ("  solution edits a protected file!" if touches_protected else ""))
        if not after:
            print(log)
    return ok


def classify(passed, stats, tampered=False):
    """One failure type per task run, for the error taxonomy."""
    if tampered:
        return "tampered"
    if passed:
        return "pass"
    if stats["stop"].startswith("api_error"):
        return "api_error"
    if stats["stop"].startswith("crash"):
        return "harness_crash"
    if stats["stop"] == "step_limit":
        return "step_limit"
    if stats["edits"] == 0:
        return "no_change"
    return "wrong_fix"


def run_task(task, model, max_steps, client=None, trace_path=None):
    import anthropic
    from agent import Agent, auto_approve
    from tracing import Tracer

    tracer = Tracer(trace_path) if trace_path else None
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        shutil.copytree(task["dir"] / "repo", ws, dirs_exist_ok=True)
        agent = Agent(workspace=ws, model=model, approver=auto_approve, max_steps=max_steps,
                      client=client, verbose=False)
        if tracer:
            tracer.event("task_start", task=task["id"], prompt=task["prompt"])
        t0 = time.time()
        try:
            stats = agent.run([{"role": "user", "content": task["prompt"]}], tracer=tracer)
        except anthropic.APIError as e:
            stats = {**EMPTY_STATS, "stop": f"api_error: {type(e).__name__}"}
        except Exception as e:
            stats = {**EMPTY_STATS, "stop": f"crash: {type(e).__name__}: {e}"}
        stats["wall_ms"] = round((time.time() - t0) * 1000)
        tampered = tampered_files(task, ws)
        passed, log = run_hidden_test(task["dir"], ws)
        if tampered:
            passed, log = False, f"agent modified protected files: {tampered}"

    p_in, p_out = PRICES.get(model, (0, 0))
    cost = stats["input_tokens"] / 1e6 * p_in + stats["output_tokens"] / 1e6 * p_out
    row = {"task": task["id"], "suite": task["suite"], "category": task["category"], "model": model,
           "passed": passed, "failure_type": classify(passed, stats, bool(tampered)),
           "tampered": tampered, "cost_usd": round(cost, 5), **stats,
           "test_log": None if passed else log}
    if tracer:
        tracer.event("task_end", passed=passed, failure_type=row["failure_type"], cost_usd=row["cost_usd"])
        tracer.close()
    return row


EMPTY_STATS = {"steps": 0, "tool_calls": 0, "edits": 0, "tool_errors": 0, "error_kinds": {},
               "input_tokens": 0, "output_tokens": 0, "model_ms": 0, "tool_ms": 0}


def summarize(rows):
    def line(name, rs):
        n = len(rs)
        pr = sum(r["passed"] for r in rs) / n
        calls = sum(r["tool_calls"] for r in rs) / n
        toks = sum(r["input_tokens"] + r["output_tokens"] for r in rs) / n
        cost = sum(r["cost_usd"] for r in rs) / n
        return f"{name:<14}{n:>4}{pr:>9.0%}{calls:>11.1f}{toks:>12,.0f}{cost:>11.4f}"

    print(f"\n{'group':<14}{'n':>4}{'pass':>9}{'tools/task':>11}{'tokens/task':>12}{'$/task':>11}")
    for key in ("suite", "category"):
        groups = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        for name, rs in sorted(groups.items()):
            print(line(name, rs))
        print()
    print(line("ALL", rows))
    print(f"\ntotal cost: ${sum(r['cost_usd'] for r in rows):.4f}")
    fails = [r for r in rows if not r["passed"]]
    if fails:
        print("failed:", ", ".join(f"{r['task']} ({r['stop']})" for r in fails))


def main():
    ap = argparse.ArgumentParser(description="Run the coding agent on the eval suite.")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--tasks", nargs="*", help="only run tasks whose id contains one of these")
    ap.add_argument("--repeat", type=int, default=1, help="runs per task (to measure variance)")
    ap.add_argument("--suite", choices=["core", "hard", "all"], default="all")
    ap.add_argument("--max-steps", type=int, default=20)
    ap.add_argument("--validate", action="store_true", help="check tasks against reference fixes; no API calls")
    args = ap.parse_args()

    try:
        import pytest  # noqa: F401
    except ImportError:
        sys.exit(f"pytest is not installed for {sys.executable}\nRun: {sys.executable} -m pip install pytest")

    tasks = load_tasks(args.tasks, args.suite)
    if not tasks:
        sys.exit("no tasks matched")
    if args.validate:
        sys.exit(0 if validate(tasks) else 1)

    from tracing import build_manifest

    run_id = f"{datetime.now():%Y%m%d-%H%M%S}_{args.model}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)
    manifest = build_manifest(run_id, args.model, args.max_steps, args.repeat, tasks, PROJECT_DIR)
    manifest["config"]["suite"] = args.suite
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if manifest["git"]["dirty"]:
        print("warning: uncommitted changes; this run is not reproducible from a commit")

    rows = []
    with (run_dir / "results.jsonl").open("w") as f:
        for i in range(args.repeat):
            for t in tasks:
                trace_path = run_dir / "traces" / f"{t['id']}__r{i}.jsonl"
                r = run_task(t, args.model, args.max_steps, trace_path=trace_path)
                r["run_id"], r["repeat"] = run_id, i
                rows.append(r)
                f.write(json.dumps(r) + "\n")
                f.flush()
                print(f"{'PASS' if r['passed'] else 'FAIL'}  {t['id']:<24} {r['failure_type']:<12} "
                      f"tools={r['tool_calls']:<3} tokens={r['input_tokens'] + r['output_tokens']:<7,} "
                      f"${r['cost_usd']:.4f}")
    summarize(rows)
    print(f"\nrun saved to {run_dir}")
    print(f"next: python report.py {run_id}")


if __name__ == "__main__":
    main()
