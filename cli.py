"""Interactive CLI: chat with the agent, review its diffs, undo whole turns."""
import argparse
import time
from datetime import datetime
from pathlib import Path

from agent import MODEL, Agent
from checkpoints import CheckpointStore, NotARepo
from pricing import cost_usd
from review import interactive_review
from tracing import Tracer, utc_ms

TRACE_DIR = Path(__file__).resolve().parent / "traces" / "cli"

HELP = """commands:
  /undo   revert every file change from the last turn that changed something
  /redo   re-apply the last undone turn
  /diff   show what the last turn changed
  /cost   tokens and cost for this session
  /help   show this
  exit    quit"""


class Session:
    def __init__(self, agent, checkpoints=None, out=print, tracer=None):
        self.agent = agent
        self.cp = checkpoints
        self.out = out
        self.tracer = tracer
        self.started = (time.time(), utc_ms())
        if tracer:
            tracer.event("run_start", task_id="interactive", suite="cli")
        self.messages = []
        self.pending_notes = []   # things the model must be told on the next turn
        self.totals = {"input_tokens": 0, "output_tokens": 0, "turns": 0}

    def command(self, line):
        """Handle a slash command. Returns True if the line was a command."""
        cmd = line.strip().lower()
        if not cmd.startswith("/"):
            return False
        if cmd == "/help":
            self.out(HELP)
        elif cmd == "/cost":
            t = self.totals
            cost = cost_usd(self.agent.model, t["input_tokens"], t["output_tokens"])
            self.out(f"{t['turns']} turns   input {t['input_tokens']:,}   output {t['output_tokens']:,}   ${cost:.4f}")
        elif cmd in ("/undo", "/redo", "/diff") and not self.cp:
            self.out("checkpoints are off: this folder is not a git repository (run `git init`)")
        elif cmd == "/diff":
            diff = self.cp.last_turn_diff()
            if diff is None:
                self.out("no turn has changed files yet")
            else:
                self.out(diff.strip() or "no changes since the last turn started")
        elif cmd == "/undo":
            label = self.cp.undo()
            if label is None:
                self.out("nothing to undo")
            else:
                self.out(f"undid: {label}   (/redo to bring it back)")
                self.pending_notes.append(
                    f"[The user ran /undo. Every file change from the turn that started with "
                    f"\"{label}\" has been reverted. Re-read files before relying on them.]")
        elif cmd == "/redo":
            label = self.cp.redo()
            if label is None:
                self.out("nothing to redo")
            else:
                self.out(f"redid: {label}")
                self.pending_notes.append(
                    f"[The user ran /redo. The file changes from \"{label}\" are back.]")
        else:
            self.out(f"unknown command {cmd}\n{HELP}")
        return True

    def turn(self, user_input):
        label = user_input.strip().splitlines()[0][:60]
        text = "\n".join(self.pending_notes + [user_input])
        self.pending_notes.clear()
        if self.cp:
            self.cp.begin_turn(label)
        self.messages.append({"role": "user", "content": text})
        try:
            stats = self.agent.run(self.messages, **({"tracer": self.tracer} if self.tracer else {}))
        finally:
            if self.cp:
                self.cp.end_turn()
        self.totals["turns"] += 1
        self.totals["input_tokens"] += stats["input_tokens"]
        self.totals["output_tokens"] += stats["output_tokens"]
        return stats

    def close(self):
        """Write run_end with session totals. Interactive sessions have no pass/fail verdict."""
        if not self.tracer:
            return
        t = self.totals
        self.tracer.event("run_end", parent=self.tracer.root_id, task_id="interactive", suite="cli",
                          input_tokens=t["input_tokens"], output_tokens=t["output_tokens"],
                          cost_usd=round(cost_usd(self.agent.model, t["input_tokens"], t["output_tokens"]), 8),
                          latency_ms=round((time.time() - self.started[0]) * 1000), started_at=self.started[1],
                          turns=t["turns"], passed=None, failure_type=None, failure_root_cause=None)
        self.tracer.close()


def main():
    ap = argparse.ArgumentParser(description="A coding agent that works in the current directory.")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--no-verify", action="store_true", help="skip lint/tests when the agent finishes")
    ap.add_argument("--no-trace", action="store_true", help="don't write a session trace to traces/cli/")
    ap.add_argument("--trace-content", action="store_true",
                    help="keep truncated tool args/outputs in the trace (default: hashes and sizes only)")
    args = ap.parse_args()

    agent = Agent(model=args.model, verify=not args.no_verify, reviewer=interactive_review)
    try:
        checkpoints = CheckpointStore(agent.root)
    except NotARepo as e:
        print(f"note: {e}")
        checkpoints = None
    tracer = None
    if not args.no_trace:
        run_id = f"cli-{datetime.now():%Y%m%d-%H%M%S}"
        tracer = Tracer(TRACE_DIR / f"{run_id}.jsonl", run_id=run_id, model=args.model,
                        trace_content=args.trace_content)
    session = Session(agent, checkpoints, tracer=tracer)
    print(f"coding agent · {args.model} · /help for commands")
    while True:
        try:
            line = input("\nYou: ")
        except EOFError:
            break
        if line.strip() in ("exit", "quit"):
            break
        if not line.strip() or session.command(line):
            continue
        session.turn(line)
    session.close()


if __name__ == "__main__":
    main()
