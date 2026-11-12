# Image the agent's commands run in with --sandbox docker (see sandbox.py).
# Build: docker build -t coding-agent-sandbox sandbox/
# Add a project's own dependencies here (or build a derived image and pass --sandbox-image).
FROM python:3.12-slim
RUN pip install --no-cache-dir "pytest>=8,<10" "ruff>=0.5"
WORKDIR /work
