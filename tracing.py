import hashlib
import json
import platform
import re
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pricing import cost_usd as price

# Event types written by the tracer. task_start/task_end/api_error are the names used
# before the analytics schema; report.py and loader.py still read them in old traces.
EVENT_TYPES = {"run_start", "model_call", "tool_call", "tool_result", "approval_decision",
               "verify", "rollback", "run_end", "api_error"}
LEGACY_TYPES = {"task_start": "run_start", "task_end": "run_end"}

# Fields every event carries, with the value used when an event has nothing to say.
ENVELOPE: dict[str, Any] = {
    "run_id": "", "session_id": "", "event_id": "", "parent_event_id": None, "ts": "", "type": "",
    "model": "", "input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "tool_name": None,
    "tool_args_hash": None, "args_size_bytes": 0, "status": "ok", "error": None, "cost_usd": 0.0,
}

# Fields that may hold file contents, prompts or model text. In the default (redacted)
# mode they are replaced by <field>_hash and <field>_size_bytes.
CONTENT_FIELDS = ("args", "output", "text", "prompt", "message", "files")

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b(\s*[=:]\s*)\S+"),
]


def sha(obj: Any, n: int = 12) -> str:
    data = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()[:n]


def size_bytes(obj: Any) -> int:
    data = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, default=str)
    return len(data.encode())


def scrub(value: Any) -> Any:
    """Mask obvious secrets even in --trace-content mode."""
    if isinstance(value, str):
        for p in SECRET_PATTERNS:
            value = p.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]" if m.re.groups else "[REDACTED]",
                          value)
        return value
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def utc_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Tracer:
    """Appends one JSON object per event to a .jsonl file.

    Every event carries the ENVELOPE fields. By default tool args/outputs, model text and
    prompts are stored only as hashes and sizes; trace_content=True keeps truncated content
    (secrets masked) for local debugging with `report.py --trace`.
    """

    def __init__(self, path: str | Path, run_id: str = "", session_id: Optional[str] = None,
                 model: str = "", trace_content: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("w")
        self.t0 = time.time()
        self.run_id = run_id
        self.session_id = session_id or uuid.uuid4().hex
        self.model = model
        self.trace_content = trace_content
        self.events: list[dict] = []   # kept in memory so run_end can derive the root cause
        self.root_id: Optional[str] = None

    def event(self, type: str, parent: Optional[str] = None, **fields: Any) -> str:
        row: dict[str, Any] = {**ENVELOPE, "t_ms": round((time.time() - self.t0) * 1000), "type": type,
                               "run_id": self.run_id, "session_id": self.session_id,
                               "event_id": uuid.uuid4().hex, "parent_event_id": parent, "ts": utc_ms(),
                               "model": self.model}
        if fields.get("tool_name") and "args" in fields:
            row["tool_args_hash"] = sha(fields["args"], 16)
            row["args_size_bytes"] = size_bytes(fields["args"])
            path = fields["args"].get("path") if isinstance(fields["args"], dict) else None
            if path:   # lets root-cause rules count edits per file without storing the path
                row["target_hash"] = sha(str(Path(path)), 16)
        if type == "model_call" and "cost_usd" not in fields:
            row["cost_usd"] = round(price(self.model, fields.get("input_tokens", 0),
                                          fields.get("output_tokens", 0)), 8)
        for k, v in fields.items():
            if k in CONTENT_FIELDS and v is not None:
                if self.trace_content:
                    row[k] = scrub(v)
                elif k == "args" and row["tool_args_hash"]:
                    continue   # already in the envelope as tool_args_hash / args_size_bytes
                else:
                    row[f"{k}_hash"] = sha(v, 16)
                    row[f"{k}_size_bytes"] = size_bytes(v)
            else:
                row[k] = v
        if type == "run_start":
            self.root_id = row["event_id"]
        self.events.append(row)
        self.f.write(json.dumps(row, default=str) + "\n")
        self.f.flush()
        return row["event_id"]

    def close(self) -> None:
        self.f.close()


def null_trace(type: str, parent: Optional[str] = None, **fields: Any) -> None:
    return None


TraceFn = Callable[..., Optional[str]]


# ---------- failure root cause ----------
# Rules, applied in this order to a failed session's events; the first match wins:
#   looping_edits     the same file was edited more than LOOP_N times, or the same tool was
#                     called with identical arguments (tool_name + tool_args_hash) more than
#                     LOOP_N times
#   premature_finish  the model ended the session itself (not the step limit) with zero
#                     successful edits, or with the completion gate failing on its last check
#                     (including a rollback)
#   bad_file_context  some tool_result failed with file_not_found or edit_mismatch, i.e. the
#                     agent worked from a wrong idea of which files exist or what they contain
#   other             none of the above (e.g. wrong fix with clean tool use, step limit
#                     without repetition, tampering, API errors)
# Passing sessions get "none".
LOOP_N = 3
CONTEXT_ERRORS = {"file_not_found", "edit_mismatch"}


def failure_root_cause(events: list[dict], passed: bool, stop: str, loop_n: int = LOOP_N) -> str:
    if passed:
        return "none"
    calls = [e for e in events if e["type"] == "tool_call"]
    results = {e["parent_event_id"]: e for e in events if e["type"] == "tool_result"}
    ok_edits = [c for c in calls if c.get("tool_name") == "edit_file"
                and results.get(c["event_id"], {}).get("status") == "ok"]

    edits_per_file = Counter(c.get("target_hash") for c in calls
                             if c.get("tool_name") == "edit_file" and c.get("target_hash"))
    repeats = Counter((c.get("tool_name"), c.get("tool_args_hash")) for c in calls)
    if any(n > loop_n for n in edits_per_file.values()) or any(n > loop_n for n in repeats.values()):
        return "looping_edits"

    verifies = [e for e in events if e["type"] == "verify"]
    gate_failed = (bool(verifies) and verifies[-1].get("ok") is False) or stop == "rolled_back"
    model_finished = stop in ("done", "rolled_back")
    if model_finished and (not ok_edits or gate_failed):
        return "premature_finish"

    if any(r.get("error") in CONTEXT_ERRORS for r in results.values()):
        return "bad_file_context"
    return "other"


def git_info(repo_dir):
    def git(*args):
        r = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    dirty = bool(git("status", "--porcelain")) if commit else None
    return {"commit": commit, "dirty": dirty}


def build_manifest(run_id, model, max_steps, repeat, tasks, project_dir):
    import anthropic
    from agent import SYSTEM, TOOLS

    task_files = {}
    for t in tasks:
        for p in sorted(t["dir"].rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                task_files[str(p.relative_to(t["dir"].parent))] = p.read_text()
    return {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "config": {"max_steps": max_steps, "repeat": repeat, "max_tokens": 4096},
        "prompt_version": sha(SYSTEM),
        "tools_version": sha(TOOLS),
        "taskset_version": sha(task_files),
        "n_tasks": len(tasks),
        "git": git_info(project_dir),
        "python": platform.python_version(),
        "anthropic_sdk": anthropic.__version__,
    }
