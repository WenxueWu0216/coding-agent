"""MCP memory server: tool schemas, structured outputs, error messages; memory tools in the agent loop."""
import json
from types import SimpleNamespace as NS

import anyio
import jsonschema
import pytest
from mcp import Client

from agent import Agent
from mcp_server import build_server
from memory import MemoryStore
from memory_policy import MemoryManager
from memory_tools import TOOLS, MemoryToolbox, anthropic_tools

EXPECTED = {"memory_save", "memory_search", "memory_get", "memory_delete", "memory_list"}


def call_all(store, calls, project="proj"):
    """Run (tool, args) calls through a real MCP client connected in memory. Returns (tools, results)."""
    server = build_server(MemoryToolbox(store, project=project, session_id="s1"))

    async def go():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            return tools, [await client.call_tool(name, args) for name, args in calls]

    return anyio.run(go)


def test_tools_have_descriptions_and_strict_schemas():
    tools, _ = call_all(MemoryStore(), [])
    assert {t.name for t in tools} == EXPECTED
    for t in tools:
        assert len(t.description) > 40
        jsonschema.Draft202012Validator.check_schema(t.input_schema)
        jsonschema.Draft202012Validator.check_schema(t.output_schema)
        assert t.input_schema["additionalProperties"] is False


def test_structured_results_match_output_schemas():
    store = MemoryStore()
    tools, results = call_all(store, [
        ("memory_save", {"content": "Staging runs on port 8080", "key": "staging_port", "tags": ["infra"],
                         "importance": 4}),
        ("memory_save", {"content": "Staging moved to port 9091", "key": "staging_port"}),
        ("memory_search", {"query": "staging port"}),
        ("memory_get", {"id": 1}),
        ("memory_list", {}),
        ("memory_delete", {"id": 1}),
    ])
    schemas = {t.name: t.output_schema for t in tools}
    names = ["memory_save", "memory_save", "memory_search", "memory_get", "memory_list", "memory_delete"]
    for name, r in zip(names, results):
        assert not r.is_error, r.content
        jsonschema.validate(r.structured_content, schemas[name])
        assert json.loads(r.content[0].text) == r.structured_content   # text mirror for older clients
    save, update, search, get, listing, delete = (r.structured_content for r in results)
    assert save["action"] == "saved" and update["action"] == "updated"
    assert update["memory"]["tags"] == ["infra"] and update["memory"]["importance"] == 4
    assert search["count"] == 1 and search["results"][0]["content"] == "Staging moved to port 9091"
    assert get["memory"]["use_count"] == 2 and listing["count"] == 1
    assert delete == {"deleted": True, "id": 1} and store.get(1) is None


@pytest.mark.parametrize("name, args, message", [
    ("memory_save", {}, "missing required argument(s): content"),
    ("memory_save", {"content": "x", "importance": 9}, "importance must be between 1 and 5; got 9"),
    ("memory_save", {"content": "x", "importance": "high"}, "importance must be of type integer, got str"),
    ("memory_save", {"content": "x", "scope": "team"}, "scope must be one of session, project, user"),
    ("memory_save", {"content": "x" * 2001}, "content is 2001 characters; the limit is 2000"),
    ("memory_save", {"content": "   "}, "content must not be empty"),
    ("memory_save", {"content": "x", "colour": "red"}, "unknown argument(s) colour"),
    ("memory_save", {"content": "x", "tags": [1, 2]}, "tags must be a list of strings"),
    ("memory_search", {"query": "x", "limit": 0}, "limit must be between 1 and 20"),
    ("memory_get", {"id": 42}, "memory #42 not found"),
    ("memory_delete", {"id": True}, "id must be of type integer, got bool"),
    ("memory_fly", {}, "unknown tool 'memory_fly'"),
])
def test_bad_calls_return_readable_errors(name, args, message):
    _, [r] = call_all(MemoryStore(), [(name, args)])
    assert r.is_error and message in r.content[0].text


def test_other_projects_memories_are_invisible():
    store = MemoryStore()
    secret = store.save("Project B's database host is db-b.internal", scope="project", namespace="project-b")
    store.save("Shared preference: tabs", scope="user")
    _, results = call_all(store, [("memory_get", {"id": secret.id}), ("memory_delete", {"id": secret.id}),
                                  ("memory_search", {"query": ""})], project="project-a")
    assert results[0].is_error and results[1].is_error
    assert [m["content"] for m in results[2].structured_content["results"]] == ["Shared preference: tabs"]
    assert store.get(secret.id) is not None


def test_secrets_are_redacted_through_mcp():
    _, [r] = call_all(MemoryStore(), [("memory_save", {"content": "deploy with token=ghp_abcdefghijklmnopqrstuvwx1234"})])
    assert "ghp_" not in r.structured_content["memory"]["content"]


# ---------- the same tools inside the agent loop ----------

def text(t):
    return NS(type="text", text=t)


def tool(i, name, inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content, stop = self.script.pop(0)
        return NS(content=content, stop_reason=stop, usage=NS(input_tokens=10, output_tokens=1))


def test_agent_gets_search_and_save_tools(tmp_path):
    store = MemoryStore()
    store.save("Billing VAT rate is 21%", scope="project", namespace="proj", importance=4)
    client = FakeClient([
        ([tool(1, "memory_search", {"query": "VAT rate"})], "tool_use"),
        ([tool(2, "memory_save", {"content": "Invoices are due in 30 days", "key": "invoice_terms",
                                  "importance": 4})], "tool_use"),
        ([tool(3, "memory_save", {"content": "x", "importance": 7})], "tool_use"),
        ([text("done")], "end_turn"),
        ([text('{"memories": []}')], "end_turn"),
    ])
    asked = []
    agent = Agent(workspace=tmp_path, approver=lambda n, a: asked.append(n) or True, client=client,
                  verbose=False, verify=False, memory=MemoryManager(store, "proj", "s1", client=client))
    s = agent.run([{"role": "user", "content": "check billing"}])
    names = [t["name"] for t in client.requests[0]["tools"]]
    assert names == ["read_file", "edit_file", "run_bash", "memory_search", "memory_save"]
    results = [b["content"] for m in client.requests[3]["messages"] if isinstance(m["content"], list)
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert results[0].startswith("Memory search results. These are notes") and "21%" in results[0]
    assert results[1] == "Memory #2 saved (project, importance 4)."
    assert results[2].startswith("Error: importance must be between 1 and 5")
    assert s["error_kinds"] == {"bad_args": 1} and asked == []   # memory tools don't prompt for approval
    assert store.get_by_key("project", "proj", "invoice_terms").content == "Invoices are due in 30 days"


def test_agent_tool_definitions_come_from_the_shared_specs():
    by_name = {t["name"]: t for t in TOOLS}
    for t in anthropic_tools():
        assert t["input_schema"] is by_name[t["name"]]["input_schema"]
