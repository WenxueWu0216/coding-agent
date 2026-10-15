import shlex
from pathlib import Path

WORKSPACE = Path.cwd().resolve()
ALLOWED_COMMANDS = {"ls", "cat", "head", "tail", "wc", "grep", "echo", "python", "python3", "pytest"}


class PolicyError(Exception):
    pass


def safe_path(path, root=WORKSPACE):
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise PolicyError(f"path '{path}' is outside the workspace")
    return p


def check_command(command):
    args = shlex.split(command)
    if not args:
        raise PolicyError("empty command")
    if args[0] not in ALLOWED_COMMANDS:
        raise PolicyError(f"command '{args[0]}' is not in the allowlist")
    for a in args[1:]:
        if a.startswith("/") or a.startswith("~") or ".." in a:
            raise PolicyError(f"argument '{a}' points outside the workspace")
    return args
