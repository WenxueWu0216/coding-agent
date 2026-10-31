"""Memory store: operations, ranking, expiry, dedup, redaction, size limits."""
import pytest

from memory import DAY, MAX_CONTENT_CHARS, MemoryInputError, MemoryStore, similarity


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, days: float) -> None:
        self.t += days * DAY


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock, tmp_path):
    s = MemoryStore(tmp_path / "memory.db", clock=clock)
    yield s
    s.close()


def test_save_get_update_delete(store):
    m = store.save("Staging runs on port 8080", scope="project", namespace="p1", key="staging_port",
                   tags=["Infra", "ports"], importance=4)
    assert m.id and m.scope == "project" and m.tags == ["infra", "ports"] and m.use_count == 0
    assert store.get(m.id).content == "Staging runs on port 8080"
    assert store.update(m.id, content="Staging runs on port 9091", importance=5).importance == 5
    assert store.get(m.id).content == "Staging runs on port 9091"
    assert store.update(999, content="x") is None
    assert store.delete(m.id) and not store.delete(m.id) and store.get(m.id) is None


def test_saving_same_key_replaces_stale_fact(store):
    a = store.save("Staging runs on port 8080", namespace="p1", key="staging_port")
    b = store.save("Staging runs on port 9091", namespace="p1", key="staging_port")
    assert a.id == b.id and len(store.list()) == 1 and store.get(a.id).content.endswith("9091")
    other = store.save("Staging runs on port 7000", namespace="p2", key="staging_port")
    assert other.id != a.id   # keys are per scope and namespace


def test_list_filters_by_scope_and_tags(store):
    store.save("prefers tuples", scope="user", tags=["style"])
    store.save("uses pytest", scope="project", namespace="p1", tags=["testing"])
    store.save("other project fact", scope="project", namespace="p2")
    assert [m.content for m in store.list({"user": ""})] == ["prefers tuples"]
    assert {m.content for m in store.list({"user": "", "project": "p1"})} == {"prefers tuples", "uses pytest"}
    assert [m.content for m in store.list(tags=["testing"])] == ["uses pytest"]


def test_search_needs_a_keyword_match(store):
    store.save("The VAT rate for billing is 21 percent", tags=["billing"])
    store.save("Deploys happen on Fridays")
    assert [m.content for m in store.search("what VAT rate do we use?")] == ["The VAT rate for billing is 21 percent"]
    assert store.search("kubernetes") == []
    assert len(store.search("")) == 2
    assert [m.content for m in store.search("rate", tags=["billing"])] == ["The VAT rate for billing is 21 percent"]


def test_ranking_more_matches_then_importance_then_recency(store, clock):
    low = store.save("timestamps are milliseconds", importance=2)
    clock.advance(1)
    high = store.save("timestamps are stored in UTC", importance=5)
    both = store.save("timestamps are UTC milliseconds", importance=1)
    assert [m.id for m in store.search("UTC milliseconds timestamps")] == [both.id, high.id, low.id]

    old = store.save("cache entries live for one hour", importance=3)
    clock.advance(30)
    new = store.save("cache entries are stored in redis", importance=3)
    assert [m.id for m in store.search("cache entries")] == [new.id, old.id]


def test_search_touches_returned_memories(store, clock):
    m = store.save("API base path is /v2")
    clock.advance(3)
    store.search("base path")
    got = store.get(m.id)
    assert got.use_count == 1 and got.last_used_at == clock.t


def test_forget_expired(store, clock):
    expiring = store.save("temporary note", importance=4, expires_at=clock.t + 2 * DAY)
    unused_low = store.save("trivia nobody needs", importance=2)
    used_low = store.save("small but used fact", importance=2)
    pinned = store.save("never forget this", importance=5, expires_at=clock.t + DAY)
    mid = store.save("medium importance fact", importance=3)
    clock.advance(20)
    store.search("small used fact")
    clock.advance(15)
    assert store.forget_expired(idle_days=30, max_importance=2) == 2
    remaining = {m.id for m in store.list(limit=100)} | {pinned.id}   # pinned is expired, so list hides it
    assert expiring.id not in remaining and unused_low.id not in remaining
    assert {used_low.id, mid.id, pinned.id} <= remaining
    assert store.get(pinned.id) is not None   # importance 5 is never auto-deleted


def test_expired_items_are_hidden_before_cleanup(store, clock):
    store.save("short lived", expires_at=clock.t + DAY)
    clock.advance(2)
    assert store.search("short lived") == [] and store.list() == []


def test_duplicates_are_detected(store):
    store.save("The user prefers functions to return tuples instead of lists", namespace="p1")
    dup = store.find_duplicate("User prefers that functions return tuples, not lists", "project", "p1")
    assert dup is not None
    assert store.find_duplicate("Staging runs on port 8080", "project", "p1") is None
    assert store.find_duplicate("The user prefers functions to return tuples instead of lists", "user") is None
    assert similarity("a b c", "") == 0.0


def test_secrets_are_redacted_before_saving(store):
    m = store.save("Deploy key is sk-ant-api03-abcdefghijklmnop and token=ghp_abcdefghijklmnopqrstuvwxyz123456")
    assert "sk-ant" not in m.content and "ghp_" not in m.content and "[REDACTED]" in m.content
    m = store.save("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123")
    assert "abcdefghijklmnop" not in m.content


@pytest.mark.parametrize("kwargs, message", [
    ({"content": ""}, "content must be a non-empty string"),
    ({"content": "x" * (MAX_CONTENT_CHARS + 1)}, f"the limit is {MAX_CONTENT_CHARS}"),
    ({"content": "ok", "scope": "team"}, "scope must be one of session, project, user"),
    ({"content": "ok", "importance": 6}, "importance must be an integer from 1 to 5"),
    ({"content": "ok", "importance": True}, "importance must be an integer from 1 to 5"),
    ({"content": "ok", "tags": "not-a-list"}, "tags must be a list of strings"),
    ({"content": "ok", "key": "k" * 101}, "key must be a non-empty string"),
])
def test_bad_input_is_rejected_with_clear_message(store, kwargs, message):
    with pytest.raises(MemoryInputError, match=message.replace("(", r"\(")):
        store.save(**kwargs)
