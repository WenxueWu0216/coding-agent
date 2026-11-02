"""Example MCP client: launch mcp_server.py over stdio and call each memory tool.

  python examples/memory_client.py            uses a throwaway database
"""
import json
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

SERVER = Path(__file__).resolve().parent.parent / "mcp_server.py"


def show(label, result):
    if result.is_error:
        print(f"{label}: {result.content[0].text}")
    else:
        print(f"{label}: {json.dumps(result.structured_content)[:300]}")


async def main(db: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER), "--db", db, "--project", "demo"])
    async with Client(params) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))
        saved = await client.call_tool("memory_save", {"content": "Staging runs on port 8080", "key": "staging_port",
                                                       "tags": ["infra"], "importance": 4})
        show("save", saved)
        show("update", await client.call_tool("memory_save", {"content": "Staging moved to port 9091",
                                                              "key": "staging_port"}))
        show("save", await client.call_tool("memory_save", {"content": "User prefers tuples over lists",
                                                            "scope": "user", "importance": 4}))
        show("search", await client.call_tool("memory_search", {"query": "staging port"}))
        mid = saved.structured_content["memory"]["id"]
        show("get", await client.call_tool("memory_get", {"id": mid}))
        show("list", await client.call_tool("memory_list", {"scope": "user"}))
        show("delete", await client.call_tool("memory_delete", {"id": mid}))
        show("bad input", await client.call_tool("memory_save", {"content": "x", "importance": 9}))
        show("missing", await client.call_tool("memory_get", {"id": mid}))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        anyio.run(main, str(Path(tmp) / "memory.db"))
