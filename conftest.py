import os
import uuid

import pytest


@pytest.fixture
def ch():
    """A throwaway database on a real ClickHouse (docker compose up -d). Skips if none is reachable."""
    try:
        import loader
        client = loader.connect(os.environ.get("CLICKHOUSE_URL", "http://localhost:8123"),
                                connect_timeout=2, send_receive_timeout=60)
        client.command("SELECT 1")
    except Exception as e:  # no server, or clickhouse-connect not installed
        pytest.skip(f"ClickHouse not reachable: {type(e).__name__}")
    db = f"test_{uuid.uuid4().hex[:10]}"
    loader.ensure_schema(client, db)
    yield client, db
    client.command(f"DROP DATABASE IF EXISTS {db}")
