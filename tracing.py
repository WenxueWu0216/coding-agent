import hashlib
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def sha(obj):
    data = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:12]


class Tracer:
    """Appends one JSON object per event to a .jsonl file."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("w")
        self.t0 = time.time()

    def event(self, type, **fields):
        row = {"t_ms": round((time.time() - self.t0) * 1000), "type": type, **fields}
        self.f.write(json.dumps(row, default=str) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


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
