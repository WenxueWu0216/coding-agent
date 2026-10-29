"""Git-backed checkpoints of the working tree, invisible to the user's own git history.

Snapshots are commits on a private ref (refs/agent/checkpoints). They are built with a
temporary index, so the user's branch, HEAD, and staging area are never touched.
"""
import os
import subprocess
import tempfile
from pathlib import Path

REF = "refs/agent/checkpoints"
IDENTITY = {"GIT_AUTHOR_NAME": "agent-checkpoint", "GIT_AUTHOR_EMAIL": "checkpoint@localhost",
            "GIT_COMMITTER_NAME": "agent-checkpoint", "GIT_COMMITTER_EMAIL": "checkpoint@localhost"}


class NotARepo(Exception):
    pass


class CheckpointStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        r = self._git("rev-parse", "--git-dir", check=False)
        if r.returncode != 0:
            raise NotARepo(f"{self.root} is not a git repository; run `git init` to enable checkpoints")
        self.git_dir = (self.root / r.stdout.strip()).resolve()
        self.undo_stack = []   # (label, commit) restore points, newest last
        self.redo_stack = []

    def _git(self, *args, env=None, check=True):
        r = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True,
                           env={**os.environ, **IDENTITY, **(env or {})})
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r

    def _with_temp_index(self, fn):
        fd, path = tempfile.mkstemp(prefix="agent-index-", dir=self.git_dir)
        os.close(fd)
        os.unlink(path)   # git creates the index itself
        try:
            return fn({"GIT_INDEX_FILE": path})
        finally:
            Path(path).unlink(missing_ok=True)

    def _tree(self):
        """Tree object for the current working tree (respects .gitignore)."""
        def build(env):
            self._git("add", "-A", ".", env=env)
            return self._git("write-tree", env=env).stdout.strip()
        return self._with_temp_index(build)

    def snapshot(self, label):
        """Record the working tree as a commit on the private ref. Returns the commit id."""
        tree = self._tree()
        parent = self._git("rev-parse", "--verify", "-q", REF, check=False).stdout.strip()
        args = ["commit-tree", tree, "-m", label] + (["-p", parent] if parent else [])
        commit = self._git(*args).stdout.strip()
        self._git("update-ref", REF, commit)
        return commit

    def changed_since(self, commit):
        """True if the working tree differs from the checkpoint."""
        return self._tree() != self._git("rev-parse", f"{commit}^{{tree}}").stdout.strip()

    def diff(self, a, b=None):
        """Unified diff between two checkpoints, or between a checkpoint and the working tree."""
        b_tree = self._tree() if b is None else f"{b}^{{tree}}"
        return self._git("diff", "--stat", "-p", f"{a}^{{tree}}", b_tree).stdout

    def restore(self, commit):
        """Make the working tree match the checkpoint: rewrite changed files, delete new ones."""
        current = self._tree()
        added = self._git("diff", "--name-only", "--diff-filter=A", f"{commit}^{{tree}}", current).stdout.split()
        for rel in added:
            (self.root / rel).unlink(missing_ok=True)

        def checkout(env):
            self._git("read-tree", f"{commit}^{{tree}}", env=env)
            self._git("checkout-index", "-a", "-f", env=env)
        self._with_temp_index(checkout)

    # ----- turn-level undo / redo -----
    def begin_turn(self, label):
        """Call before the agent starts a turn."""
        self.undo_stack.append((label, self.snapshot(f"before: {label}")))
        self.redo_stack.clear()

    def end_turn(self):
        """Drop the restore point if the turn changed nothing, so /undo skips no-op turns."""
        if self.undo_stack and not self.changed_since(self.undo_stack[-1][1]):
            self.undo_stack.pop()

    def last_turn_diff(self):
        if not self.undo_stack:
            return None
        return self.diff(self.undo_stack[-1][1])

    def undo(self):
        """Revert the most recent turn that changed files. Returns its label, or None."""
        if not self.undo_stack:
            return None
        label, before = self.undo_stack.pop()
        self.redo_stack.append((label, self.snapshot(f"before undo: {label}")))
        self.restore(before)
        return label

    def redo(self):
        if not self.redo_stack:
            return None
        label, after = self.redo_stack.pop()
        self.undo_stack.append((label, self.snapshot(f"before redo: {label}")))
        self.restore(after)
        return label
