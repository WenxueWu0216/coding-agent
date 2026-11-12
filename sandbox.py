"""Where the agent's commands run.

Everything that executes code the agent wrote goes through a runner: `run_bash`, the completion
gate's lint and test runs (verify.py), and the eval's hidden tests (eval.py).

- `LocalRunner` (`--sandbox none`) runs commands directly on this machine, as before. The command
  allowlist and approval gate still apply, but `python` and `pytest` can run arbitrary code, which can
  read environment variables (API keys), files outside the workspace, and the network.
- `DockerSandbox` (`--sandbox docker`) runs each command in a fresh, throwaway container that sees
  only the workspace (mounted at /work), has no network, a read-only root filesystem, no Linux
  capabilities, no environment variables from the host, and capped memory, CPU and process count.
  The kernel enforces these limits, so they hold whatever code runs inside.

The container still has read-write access to the workspace: the agent has to be able to change
project files. /undo checkpoints, the completion gate's rollback and the approval gate cover that.
"""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

DEFAULT_IMAGE = "coding-agent-sandbox"
WORKDIR = "/work"
# Only these reach the container. Nothing else from the host environment is passed in.
CONTAINER_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp", "PYTHONUNBUFFERED": "1"}
DOCKER_ERROR = 125   # `docker run` exit code when the container could not be started


class SandboxUnavailable(Exception):
    pass


class LocalRunner:
    """Runs commands on the host (no isolation)."""
    name = "none"

    def python(self):
        return [sys.executable]

    def ruff(self):
        if shutil.which("ruff"):
            return ["ruff"]
        try:
            import ruff  # noqa: F401
            return [sys.executable, "-m", "ruff"]
        except ImportError:
            return None

    def describe(self):
        return {"sandbox": self.name}

    def run(self, args, cwd, timeout):
        """Returns (exit code, stdout + stderr). Raises subprocess.TimeoutExpired."""
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return r.returncode, r.stdout + r.stderr


class DockerSandbox:
    """Runs each command in a new container: docker run --rm ... <image> <args>."""
    name = "docker"

    def __init__(self, image=DEFAULT_IMAGE, memory="512m", cpus="1", pids_limit=128, tmp_size="64m",
                 docker="docker"):
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.tmp_size = tmp_size
        self.docker = docker

    def python(self):
        return ["python"]

    def ruff(self):
        return ["python", "-m", "ruff"]   # installed in the image (sandbox/Dockerfile)

    def describe(self):
        return {"sandbox": self.name, "image": self.image, "memory": self.memory, "cpus": self.cpus,
                "pids_limit": self.pids_limit}

    def command(self, args, cwd, name):
        """The full `docker run` argument list. Kept separate so tests can check every flag."""
        cwd = Path(cwd).resolve()
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
        cmd = [
            self.docker, "run", "--rm", "--name", name,
            "--network", "none",                                  # no network at all
            "--read-only",                                        # root filesystem is read-only...
            "--tmpfs", f"/tmp:rw,nosuid,size={self.tmp_size}",    # ...except a small scratch /tmp
            "--mount", f"type=bind,source={cwd},target={WORKDIR}",   # the workspace, and nothing else
            "--workdir", WORKDIR,
            "--cap-drop", "ALL",                                  # no Linux capabilities
            "--security-opt", "no-new-privileges",                # setuid binaries can't raise privileges
            "--memory", self.memory, "--memory-swap", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),                 # stops fork bombs
        ]
        if user:
            cmd += ["--user", user]   # files the agent creates are owned by you, not root
        for k, v in CONTAINER_ENV.items():
            cmd += ["--env", f"{k}={v}"]
        return cmd + [self.image, *args]

    def run(self, args, cwd, timeout):
        """Returns (exit code, stdout + stderr). Raises subprocess.TimeoutExpired, after killing the
        container: killing only the docker CLI would leave the container running."""
        name = f"coding-agent-{uuid.uuid4().hex[:12]}"
        try:
            r = subprocess.run(self.command(args, cwd, name), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run([self.docker, "rm", "-f", name], capture_output=True, timeout=30)
            raise
        out = r.stdout + r.stderr
        if r.returncode == DOCKER_ERROR and "docker" in r.stderr.lower():
            out += "\n[sandbox error: the container could not be started; see the message above]"
        return r.returncode, out

    def check(self):
        """Raise SandboxUnavailable with a fix if Docker or the image is missing."""
        if not shutil.which(self.docker):
            raise SandboxUnavailable("Docker is not installed. Install it, or run with --sandbox none "
                                     "(commands then run directly on this machine).")
        try:
            info = subprocess.run([self.docker, "info", "--format", "{{.ServerVersion}}"],
                                  capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            raise SandboxUnavailable("Docker did not respond to `docker info`.")
        if info.returncode != 0:
            raise SandboxUnavailable("The Docker daemon is not running. Start Docker, or run with --sandbox none.\n"
                                     + info.stderr.strip()[-300:])
        image = subprocess.run([self.docker, "image", "inspect", self.image], capture_output=True, timeout=20)
        if image.returncode != 0:
            raise SandboxUnavailable(f"Sandbox image '{self.image}' not found. Build it once with:\n"
                                     f"  docker build -t {self.image} sandbox/")


def make_runner(kind, image=DEFAULT_IMAGE):
    """`kind` is 'docker' or 'none'. A Docker sandbox is checked before it is returned."""
    if kind == "none":
        return LocalRunner()
    if kind == "docker":
        box = DockerSandbox(image=image)
        box.check()
        return box
    raise ValueError(f"unknown sandbox {kind!r}")
