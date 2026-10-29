"""Tests for git checkpoints, /undo, /redo, and the CLI session."""
import subprocess
from types import SimpleNamespace as NS

import pytest

from checkpoints import REF, CheckpointStore, NotARepo
from cli import Session


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True,
                          env={"GIT_AUTHOR_NAME": "u", "GIT_AUTHOR_EMAIL": "u@x", "GIT_COMMITTER_NAME": "u",
                               "GIT_COMMITTER_EMAIL": "u@x", "PATH": "/usr/bin:/bin:/usr/local/bin"}).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("secret.env\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_not_a_repo(tmp_path):
    with pytest.raises(NotARepo):
        CheckpointStore(tmp_path)


def test_undo_restores_edits_and_removes_new_files(repo):
    cp = CheckpointStore(repo)
    cp.begin_turn("refactor")
    (repo / "a.py").write_text("x = 2\n")
    (repo / "b.py").write_text("y = 1\n")
    (repo / "a.py.bak").write_text("tmp\n")
    cp.end_turn()
    assert cp.undo() == "refactor"
    assert (repo / "a.py").read_text() == "x = 1\n"
    assert not (repo / "b.py").exists() and not (repo / "a.py.bak").exists()


def test_redo_brings_changes_back(repo):
    cp = CheckpointStore(repo)
    cp.begin_turn("t")
    (repo / "a.py").write_text("x = 2\n")
    (repo / "b.py").write_text("y = 1\n")
    cp.end_turn()
    cp.undo()
    assert cp.redo() == "t"
    assert (repo / "a.py").read_text() == "x = 2\n" and (repo / "b.py").exists()
    assert cp.undo() == "t" and (repo / "a.py").read_text() == "x = 1\n"


def test_users_git_state_is_untouched(repo):
    (repo / "staged.py").write_text("s = 1\n")
    git(repo, "add", "staged.py")                       # user has something staged
    head, status = git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")
    cp = CheckpointStore(repo)
    cp.begin_turn("t")
    (repo / "a.py").write_text("x = 3\n")
    cp.end_turn()
    cp.undo()
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == status   # staging area preserved
    assert git(repo, "log", "--oneline", REF)              # checkpoints live on a private ref
    assert "agent-checkpoint" not in git(repo, "log", "--format=%an", "HEAD")


def test_ignored_files_are_never_touched(repo):
    (repo / "secret.env").write_text("KEY=1\n")
    cp = CheckpointStore(repo)
    cp.begin_turn("t")
    (repo / "a.py").write_text("x = 9\n")
    cp.end_turn()
    cp.undo()
    assert (repo / "secret.env").read_text() == "KEY=1\n"


def test_noop_turns_are_skipped(repo):
    cp = CheckpointStore(repo)
    cp.begin_turn("edit")
    (repo / "a.py").write_text("x = 2\n")
    cp.end_turn()
    cp.begin_turn("just a question")
    cp.end_turn()
    assert cp.undo() == "edit"
    assert cp.undo() is None


def test_last_turn_diff(repo):
    cp = CheckpointStore(repo)
    cp.begin_turn("t")
    (repo / "a.py").write_text("x = 2\n")
    cp.end_turn()
    d = cp.last_turn_diff()
    assert "-x = 1" in d and "+x = 2" in d


class FakeAgent:
    """Edits a.py each turn and remembers what it was told."""
    model = "claude-haiku-5-5"

    def __init__(self, repo):
        self.repo, self.seen, self.n = repo, [], 0

    def run(self, messages):
        self.seen.append(messages[-1]["content"])
        self.n += 1
        (self.repo / "a.py").write_text(f"x = {self.n + 1}\n")
        return {"input_tokens": 1000, "output_tokens": 100}


def test_session_undo_tells_the_model(repo):
    out = []
    agent = FakeAgent(repo)
    s = Session(agent, CheckpointStore(repo), out=out.append)
    s.turn("change x")
    assert s.command("/undo")
    assert (repo / "a.py").read_text() == "x = 1\n"
    s.turn("try again")
    assert "/undo" in agent.seen[-1] and agent.seen[-1].endswith("try again")
    s.command("/cost")
    assert "2 turns" in out[-1] and "$0.0003" in out[-1]


def test_session_without_git(tmp_path):
    out = []
    s = Session(FakeAgent(tmp_path), None, out=out.append)
    s.command("/undo")
    assert "not a git repository" in out[-1]
    assert not s.command("hello")
