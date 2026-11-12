"""Completion gate: when the agent says it's done, lint and test its changes.

New failures are fed back to the model. After too many failed attempts in a row,
every edit from the current turn is rolled back so the repo is never left worse
than the agent found it.
"""
import re
import subprocess
from pathlib import Path

from sandbox import LocalRunner

# Ruff rules for errors that are almost always real bugs: syntax errors,
# invalid comparisons, misplaced statements, and undefined names.
FATAL_RUFF_RULES = "E9,F63,F7,F82"
PYTEST_SUMMARY = re.compile(r"^(?:FAILED|ERROR) (\S+)")
RUFF_ERROR = re.compile(r"^\S+:\d+:\d+: [A-Z]+\d+")


def _run(args, root, runner, timeout=60):
    try:
        return runner.run(args, cwd=root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout}s"


def lint(root, files, runner=None):
    """Lint only the files the agent changed. Returns (failure ids, output)."""
    runner = runner or LocalRunner()
    py = [f for f in files if f.endswith(".py") and (root / f).exists()]
    if not py:
        return set(), ""
    ruff = runner.ruff()
    if ruff:
        code, out = _run(ruff + ["check", "--select", FATAL_RUFF_RULES, "--output-format", "concise",
                                 "--no-cache", *py], root, runner)
    else:
        code, out = _run(runner.python() + ["-m", "py_compile", *py], root, runner)
    if code == 0:
        return set(), out
    errors = {m.group(0) for line in out.splitlines() if (m := RUFF_ERROR.match(line))}
    return (errors or {f"lint failed (exit code {code})"}), out


def run_tests(root, runner=None):
    """Run the project's tests. Returns (ids of failing tests, output)."""
    runner = runner or LocalRunner()
    code, out = _run(runner.python() + ["-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider"], root, runner)
    if code in (0, 5):  # 5 = no tests collected
        return set(), out
    failing = {m.group(1) for line in out.splitlines() if (m := PYTEST_SUMMARY.match(line))}
    return (failing or {f"pytest exit code {code}"}), out


class Verifier:
    def __init__(self, root, max_attempts=3, log=None, runner=None):
        self.root = Path(root).resolve()
        self.runner = runner or LocalRunner()
        self.max_attempts = max_attempts
        self.log = log or (lambda msg: None)
        self.reset()

    def reset(self):
        """Call at the start of every user turn."""
        self.originals = {}      # Path -> original bytes, or None if the agent created the file
        self.baseline = None     # failing test ids before the agent touched anything
        self.dirty = False       # edits since the last check
        self.strikes = 0         # failed checks in a row

    def before_write(self, path):
        """Called by edit_file right before it writes. Records state needed for rollback."""
        if self.baseline is None:
            self.baseline, _ = run_tests(self.root, self.runner)
        if path not in self.originals:
            self.originals[path] = path.read_bytes() if path.exists() else None
        self.dirty = True

    def changed_files(self):
        return sorted(str(p.relative_to(self.root)) for p in self.originals)

    def check(self):
        """Returns (new_failures, report). Only failures the agent introduced count."""
        self.dirty = False
        lint_fails, lint_out = lint(self.root, self.changed_files(), self.runner)
        test_fails, test_out = run_tests(self.root, self.runner)
        new_tests = test_fails - (self.baseline or set())
        new = sorted(lint_fails) + sorted(new_tests)
        report = []
        if lint_fails:
            report.append("Lint errors:\n" + lint_out.strip()[-1500:])
        if new_tests:
            report.append("New test failures:\n" + test_out.strip()[-2500:])
        return new, "\n\n".join(report)

    def rollback(self):
        """Restore every file edited this turn; delete files the agent created."""
        restored = self.changed_files()
        for path, data in self.originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        self.originals = {}
        self.dirty = False
        return restored
