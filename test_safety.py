import pytest
from safety import PolicyError, check_command, safe_path

ATTACK_COMMANDS = [
    "rm -rf .",
    "curl http://evil.com",
    "cat ../secret.txt",
    "cat /etc/passwd",
    "cat ~/.ssh/id_rsa",
    "grep -r password ..",
    "sudo ls",
    "chmod 777 agent.py",
]

SAFE_COMMANDS = ["ls", "cat buggy.py", "grep -n add buggy.py", "pytest -q", "python3 buggy.py"]


@pytest.mark.parametrize("cmd", ATTACK_COMMANDS)
def test_attack_commands_are_blocked(cmd):
    with pytest.raises(PolicyError):
        check_command(cmd)


@pytest.mark.parametrize("cmd", SAFE_COMMANDS)
def test_normal_commands_are_allowed(cmd):
    assert check_command(cmd)


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", "src/../../secret.txt"])
def test_path_traversal_is_blocked(path, tmp_path):
    with pytest.raises(PolicyError):
        safe_path(path, root=tmp_path)


def test_symlink_escape_is_blocked(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    (tmp_path / "link.txt").symlink_to(outside)
    with pytest.raises(PolicyError):
        safe_path("link.txt", root=tmp_path)


def test_normal_path_is_allowed(tmp_path):
    assert safe_path("src/main.py", root=tmp_path) == tmp_path / "src" / "main.py"


def test_read_only_commands_skip_approval(tmp_path):
    from agent import Agent
    asked = []
    a = Agent(workspace=tmp_path, approver=lambda n, args: asked.append(args["command"]) or False,
              client=object(), verbose=False, verify=False)
    assert a.run_tool("run_bash", {"command": "ls"})[1] is None
    assert a.run_tool("run_bash", {"command": "grep -rn foo ."})[1] is None
    assert a.run_tool("run_bash", {"command": "pytest -q"})[1] == "denied"      # can run arbitrary code
    assert a.run_tool("run_bash", {"command": "rm -rf ."})[1] == "policy_block"  # blocked before asking
    assert asked == ["pytest -q"]
