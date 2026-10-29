"""Hunk-level review of proposed edits.

Every edit is shown as a colored diff split into hunks. For each hunk the user can
accept, reject, or edit it, and only the chosen result is written to disk.
"""
import difflib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field


@dataclass
class Hunk:
    i1: int          # start line in the old text
    i2: int          # end line (exclusive) in the old text
    j1: int          # start line in the new text
    j2: int          # end line (exclusive) in the new text
    lines: list = field(default_factory=list)   # rendered diff lines, with context


@dataclass
class ReviewResult:
    text: str        # final file content to write
    accepted: int
    rejected: int
    edited: int

    @property
    def total(self):
        return self.accepted + self.rejected + self.edited

    def summary(self):
        if self.accepted == self.total:
            return "The user accepted all changes."
        parts = [f"{self.accepted} accepted", f"{self.rejected} rejected"]
        if self.edited:
            parts.append(f"{self.edited} edited by the user")
        return (f"The user reviewed {self.total} hunk(s): {', '.join(parts)}. "
                "Rejected hunks were NOT applied; re-read the file before making further edits.")


def make_hunks(old, new, context=3):
    """Split the change from old to new into hunks, like `diff -u`."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    hunks = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(context):
        changes = [op for op in group if op[0] != "equal"]
        h = Hunk(changes[0][1], changes[-1][2], changes[0][3], changes[-1][4])
        first, last = group[0], group[-1]
        h.lines.append(f"@@ -{first[1] + 1},{last[2] - first[1]} +{first[3] + 1},{last[4] - first[3]} @@")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                h.lines += [" " + l.rstrip("\n") for l in a[i1:i2]]
            else:
                h.lines += ["-" + l.rstrip("\n") for l in a[i1:i2]]
                h.lines += ["+" + l.rstrip("\n") for l in b[j1:j2]]
        hunks.append(h)
    return hunks


def apply_hunks(old, new, hunks, decisions):
    """Build the final text. decisions[k] is "y" (take new), "n" (keep old),
    or a list of replacement lines written by the user."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    out, pos = [], 0
    for h, d in zip(hunks, decisions):
        out += a[pos:h.i1]                       # unchanged lines before this hunk
        if d == "y":
            out += b[h.j1:h.j2]
        elif d == "n":
            out += a[h.i1:h.i2]
        else:
            out += d
        pos = h.i2
    out += a[pos:]
    return "".join(out)


def _color(line, enabled):
    if not enabled:
        return line
    code = {"+": "32", "-": "31", "@": "36"}.get(line[:1])
    return f"\033[{code}m{line}\033[0m" if code else line


def _edit_in_editor(lines):
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as f:
        f.write("".join(lines))
        name = f.name
    try:
        subprocess.run([editor, name], check=False)
        with open(name) as f:
            return f.read().splitlines(keepends=True)
    finally:
        os.unlink(name)


HELP = "y = accept, n = reject, e = edit, a = accept this and all remaining, d = reject this and all remaining"


def interactive_review(path, old, new, ask=input, out=None, editor=_edit_in_editor):
    """Show each hunk and ask what to do with it. Returns a ReviewResult."""
    out = out or sys.stdout
    color = out.isatty() and "NO_COLOR" not in os.environ
    hunks = make_hunks(old, new)
    if not hunks:
        return ReviewResult(old, 0, 0, 0)
    adds = sum(l.startswith("+") for h in hunks for l in h.lines[1:])
    dels = sum(l.startswith("-") for h in hunks for l in h.lines[1:])
    print(_color(f"\n── {path}  +{adds} -{dels}  ({len(hunks)} hunk{'s' * (len(hunks) > 1)})", color), file=out)

    decisions, rest = [], None
    b = new.splitlines(keepends=True)
    for k, h in enumerate(hunks, 1):
        if rest:
            decisions.append(rest)
            continue
        print("\n".join(_color(l, color) for l in h.lines), file=out)
        while True:
            choice = ask(f"  hunk {k}/{len(hunks)} [y,n,e,a,d,?] ").strip().lower()
            if choice in ("y", "n"):
                decisions.append(choice)
            elif choice in ("a", "d"):
                rest = "y" if choice == "a" else "n"
                decisions.append(rest)
            elif choice == "e":
                decisions.append(editor(b[h.j1:h.j2]))
            else:
                print("  " + HELP, file=out)
                continue
            break

    text = apply_hunks(old, new, hunks, decisions)
    return ReviewResult(
        text,
        accepted=sum(d == "y" for d in decisions),
        rejected=sum(d == "n" for d in decisions),
        edited=sum(isinstance(d, list) for d in decisions),
    )
