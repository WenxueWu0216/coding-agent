"""The Docker sandbox: commands the allowlist lets through (python, pytest) can run any code, so the
container has to contain them. Each attack below succeeds with --sandbox none and must fail here.

The first tests check the `docker run` flags and need nothing. The rest run real containers and are
skipped if Docker or the image is missing: docker build -t coding-agent-sandbox sandbox/
"""
import os
import subprocess
import time

import pytest

from agent import Agent, auto_approve
from sandbox import DEFAULT_IMAGE, DockerSandbox, LocalRunner, SandboxUnavailable, make_runner
from verify import Verifier, lint, run_tests

FAKE_KEY = "sk-ant-sandbox-test-0000"


# ---------- flags (no Docker needed) ----------
def flags(tmp_path):
    return DockerSandbox().command(["python", "-V"], tmp_path, "c1")


def test_command_isolates_network_filesystem_and_privileges(tmp_path):
    cmd = flags(tmp_path)
    joined = " ".join(cmd)
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "--network none" in joined
    assert "--read-only" in cmd
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert f"--pids-limit {DockerSandbox().pids_limit}" in joined
    assert "--memory 512m" in joined and "--memory-swap 512m" in joined
    assert cmd[-3:] == [DEFAULT_IMAGE, "python", "-V"]


def test_only_the_workspace_is_mounted(tmp_path):
    cmd = flags(tmp_path)
    mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a in ("--mount", "-v", "--volume")]
    assert mounts == [f"type=bind,source={tmp_path.resolve()},target=/work"]


def test_no_host_environment_is_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    cmd = flags(tmp_path)
    envs = [cmd[i + 1] for i, a in enumerate(cmd) if a in ("--env", "-e")]
    assert all(e.split("=")[0] in ("PYTHONDONTWRITEBYTECODE", "HOME", "PYTHONUNBUFFERED") for e in envs)
    assert "--env-file" not in cmd and FAKE_KEY not in " ".join(cmd)


def test_missing_docker_gives_a_fix(monkeypatch):
    with pytest.raises(SandboxUnavailable, match="--sandbox none"):
        DockerSandbox(docker="docker-that-does-not-exist").check()


def test_make_runner():
    assert isinstance(make_runner("none"), LocalRunner)
    with pytest.raises(ValueError):
        make_runner("vm")


# ---------- real containers ----------
@pytest.fixture(scope="module")
def box():
    sandbox = DockerSandbox()
    try:
        sandbox.check()
    except SandboxUnavailable as e:
        pytest.skip(str(e).splitlines()[0])
    return sandbox


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "secret.txt").write_text("TOP SECRET")   # outside the workspace
    work = tmp_path / "ws"
    work.mkdir()
    return work


def bash(box, ws, command):
    agent = Agent(workspace=ws, approver=auto_approve, client=object(), verbose=False, verify=False, sandbox=box)
    out, err = agent.run_tool("run_bash", {"command": command})
    assert err is None, out   # the allowlist lets every one of these through; the sandbox must stop them
    return out


def py(code):
    return f'python -c "{code}"'


def test_environment_secrets_are_not_visible(box, ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    out = bash(box, ws, py("import os; print(sorted(os.environ)); print(os.environ.get('ANTHROPIC_API_KEY'))"))
    assert "exit code 0" in out
    assert FAKE_KEY not in out and "ANTHROPIC_API_KEY" not in out


def test_files_outside_the_workspace_are_not_reachable(box, ws):
    # os.pardir avoids a literal '..', which check_command would reject before the sandbox is reached
    out = bash(box, ws, py("import os; os.chdir(os.pardir); print(open('secret.txt').read())"))
    assert "TOP SECRET" not in out
    assert "FileNotFoundError" in out


def test_host_home_is_not_visible(box, ws):
    home = os.path.expanduser("~")
    out = bash(box, ws, py(f"import os; print(os.path.exists({home!r} + '/.ssh'), os.listdir({home!r}) if os.path.isdir({home!r}) else 'none')"))
    assert "False" in out


def test_no_network(box, ws):
    out = bash(box, ws, py("import socket; socket.create_connection(('1.1.1.1', 443), timeout=3)"))
    assert "exit code 0" not in out
    assert "OSError" in out or "unreachable" in out.lower()


def test_no_dns(box, ws):
    out = bash(box, ws, py("import socket; socket.getaddrinfo('example.com', 443)"))
    assert "exit code 0" not in out and "gaierror" in out


def test_root_filesystem_is_read_only(box, ws):
    out = bash(box, ws, py("open('/usr/local/lib/evil.py', 'w').write('x')"))
    assert "exit code 0" not in out
    assert "Read-only file system" in out or "Permission denied" in out


def test_no_capabilities(box, ws):
    out = bash(box, ws, py("print(open('/proc/self/status').read())"))
    cap_eff = next(line for line in out.splitlines() if line.startswith("CapEff:"))
    assert int(cap_eff.split()[1], 16) == 0


def test_process_limit_stops_a_fork_bomb(box, ws):
    code = ("import os, time\\nn = 0\\ntry:\\n    for _ in range(400):\\n        if os.fork() == 0:\\n"
            "            time.sleep(5); os._exit(0)\\n        n += 1\\nexcept OSError:\\n    pass\\nprint('forked', n)")
    (ws / "bomb.py").write_text(code.replace("\\n", "\n"))
    out = bash(box, ws, "python bomb.py")
    forked = int(out.split("forked")[1].split()[0])
    assert forked < box.pids_limit


def test_workspace_is_writable_and_files_belong_to_the_user(box, ws):
    out = bash(box, ws, py("open('made_inside.txt', 'w').write('hello')"))
    assert "exit code 0" in out
    made = ws / "made_inside.txt"
    assert made.read_text() == "hello"
    if hasattr(os, "getuid"):
        assert made.stat().st_uid == os.getuid()


def test_pytest_runs_inside(box, ws):
    (ws / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    out = bash(box, ws, "pytest -q")
    assert "exit code 0" in out and "1 passed" in out


def test_allowlist_still_applies(box, ws):
    agent = Agent(workspace=ws, approver=auto_approve, client=object(), verbose=False, verify=False, sandbox=box)
    out, err = agent.run_tool("run_bash", {"command": "curl http://example.com"})
    assert err == "policy_block"


def test_timeout_kills_the_container(box, ws):
    before = set(subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True).stdout.split())
    with pytest.raises(subprocess.TimeoutExpired):
        box.run(["python", "-c", "import time; time.sleep(60)"], cwd=ws, timeout=3)
    time.sleep(1)
    after = set(subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True).stdout.split())
    assert after <= before


def test_completion_gate_runs_in_the_sandbox(box, ws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    # A test that leaks the key if it can see it, and fails if it can't: inside the sandbox it must fail.
    (ws / "test_leak.py").write_text(
        "import os\ndef test_leak():\n    assert os.environ.get('ANTHROPIC_API_KEY')\n")
    failing, out = run_tests(ws, box)
    assert failing == {"test_leak.py::test_leak"}
    (ws / "bad.py").write_text("print(undefined_name)\n")
    lint_fails, _ = lint(ws, ["bad.py"], box)
    assert lint_fails
    assert Verifier(ws, runner=box).runner is box


def test_hidden_tests_run_in_the_sandbox(box, ws, monkeypatch, tmp_path):
    import eval as ev
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    task = tmp_path / "task"
    task.mkdir()
    (task / "hidden_test.py").write_text("import os\ndef test_hidden():\n    assert 'ANTHROPIC_API_KEY' not in os.environ\n")
    passed, log = ev.run_hidden_test(task, ws, box)
    assert passed, log
