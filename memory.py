"""Long-term memory for the agent, stored in SQLite.

Memories have a scope: "session" (one conversation), "project" (one workspace) or "user"
(everywhere). The namespace column says which session or project a memory belongs to, so
one database can serve many projects.
"""
from __future__ import annotations  # the store has a method named list

import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from tracing import scrub

SCOPES = ("session", "project", "user")
MAX_CONTENT_CHARS = 2000
MAX_KEY_CHARS = 100
MAX_TAGS = 10
DAY = 86400.0
STOPWORDS = {"the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had", "her", "was", "one",
             "our", "out", "has", "have", "this", "that", "with", "from", "they", "will", "would", "there",
             "their", "what", "when", "which", "into", "than", "then", "them", "these", "some", "does", "about",
             "just", "please", "should", "could", "make", "also", "use", "using", "add", "file", "function"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope        TEXT NOT NULL CHECK (scope IN ('session', 'project', 'user')),
    namespace    TEXT NOT NULL DEFAULT '',
    key          TEXT,
    content      TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    last_used_at REAL NOT NULL,
    use_count    INTEGER NOT NULL DEFAULT 0,
    importance   INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    expires_at   REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS memories_key ON memories (scope, namespace, key) WHERE key IS NOT NULL;
"""


class MemoryInputError(ValueError):
    """Bad input from a caller (the model, an MCP client). The message is shown to them."""


@dataclass
class Memory:
    id: int
    scope: str
    namespace: str
    key: Optional[str]
    content: str
    tags: list[str]
    created_at: float
    last_used_at: float
    use_count: int
    importance: int
    expires_at: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("created_at", "last_used_at", "expires_at"):
            d[k] = iso(d[k]) if d[k] is not None else None
        return d


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if len(w) >= 3 and w not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of the two texts' word sets."""
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def normalize_tags(tags: Optional[Iterable[str]]) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str) or not all(isinstance(t, str) for t in tags):
        raise MemoryInputError("tags must be a list of strings")
    out = sorted({re.sub(r"[^a-z0-9_\-]", "", t.strip().lower()) for t in tags} - {""})
    if len(out) > MAX_TAGS:
        raise MemoryInputError(f"at most {MAX_TAGS} tags are allowed, got {len(out)}")
    return out


class MemoryStore:
    def __init__(self, path: str | Path = ":memory:", clock=time.time):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.clock = clock

    def close(self) -> None:
        self.db.close()

    # ---------- validation ----------
    @staticmethod
    def _check(scope: str, content: Optional[str], importance: Optional[int], key: Optional[str]) -> None:
        if scope not in SCOPES:
            raise MemoryInputError(f"scope must be one of {', '.join(SCOPES)}; got {scope!r}")
        if content is not None:
            if not isinstance(content, str) or not content.strip():
                raise MemoryInputError("content must be a non-empty string")
            if len(content) > MAX_CONTENT_CHARS:
                raise MemoryInputError(f"content is {len(content)} characters; the limit is {MAX_CONTENT_CHARS}. "
                                       "Save a shorter summary.")
        if importance is not None and (isinstance(importance, bool) or not isinstance(importance, int)
                                       or not 1 <= importance <= 5):
            raise MemoryInputError(f"importance must be an integer from 1 to 5; got {importance!r}")
        if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_CHARS):
            raise MemoryInputError(f"key must be a non-empty string of at most {MAX_KEY_CHARS} characters")

    def _row(self, r: sqlite3.Row) -> Memory:
        return Memory(**{**dict(r), "tags": [t for t in r["tags"].split(",") if t]})

    # ---------- operations ----------
    def save(self, content: str, scope: str = "project", namespace: str = "", key: Optional[str] = None,
             tags: Optional[Iterable[str]] = None, importance: int = 3,
             expires_at: Optional[float] = None) -> Memory:
        """Save a memory. Saving with a key that already exists in the same scope replaces that memory's
        content, so a changed fact overwrites the stale one instead of contradicting it."""
        self._check(scope, content, importance, key)
        content = scrub(content.strip())   # never persist API keys or tokens
        tag_str = ",".join(normalize_tags(tags))
        now = self.clock()
        if key:
            existing = self.db.execute("SELECT id FROM memories WHERE scope = ? AND namespace = ? AND key = ?",
                                       (scope, namespace, key)).fetchone()
            if existing:
                self.db.execute("UPDATE memories SET content = ?, tags = ?, importance = ?, expires_at = ?, "
                                "last_used_at = ? WHERE id = ?",
                                (content, tag_str, importance, expires_at, now, existing["id"]))
                self.db.commit()
                return self.get(existing["id"])
        cur = self.db.execute(
            "INSERT INTO memories (scope, namespace, key, content, tags, created_at, last_used_at, importance, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scope, namespace, key, content, tag_str, now, now, importance, expires_at))
        self.db.commit()
        return self.get(cur.lastrowid)

    def get(self, memory_id: int, touch: bool = False) -> Optional[Memory]:
        r = self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if r and touch:
            self._touch([memory_id])
            r = self.db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row(r) if r else None

    def update(self, memory_id: int, content: Optional[str] = None, tags: Optional[Iterable[str]] = None,
               importance: Optional[int] = None, expires_at: Optional[float] = None,
               key: Optional[str] = None) -> Optional[Memory]:
        current = self.get(memory_id)
        if not current:
            return None
        self._check(current.scope, content, importance, key)
        fields: dict[str, Any] = {}
        if content is not None:
            fields["content"] = scrub(content.strip())
        if tags is not None:
            fields["tags"] = ",".join(normalize_tags(tags))
        if importance is not None:
            fields["importance"] = importance
        if expires_at is not None:
            fields["expires_at"] = expires_at
        if key is not None:
            fields["key"] = key
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE memories SET {sets} WHERE id = ?", (*fields.values(), memory_id))
            self.db.commit()
        return self.get(memory_id)

    def delete(self, memory_id: int) -> bool:
        cur = self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.db.commit()
        return cur.rowcount > 0

    def _filter(self, scopes: Optional[dict[str, str]], tags: Optional[Iterable[str]]) -> tuple[str, list]:
        """WHERE clause for {scope: namespace} pairs and required tags (all must match)."""
        where, args = ["(expires_at IS NULL OR expires_at > ?)"], [self.clock()]
        if scopes:
            where.append("(" + " OR ".join("(scope = ? AND namespace = ?)" for _ in scopes) + ")")
            for s, ns in scopes.items():
                args += [s, ns]
        for t in normalize_tags(tags):
            where.append("(',' || tags || ',') LIKE ?")
            args.append(f"%,{t},%")
        return " AND ".join(where), args

    def list(self, scopes: Optional[dict[str, str]] = None, tags: Optional[Iterable[str]] = None,
             limit: int = 50) -> list[Memory]:
        where, args = self._filter(scopes, tags)
        rows = self.db.execute(f"SELECT * FROM memories WHERE {where} "
                               "ORDER BY importance DESC, last_used_at DESC, id DESC LIMIT ?", (*args, limit))
        return [self._row(r) for r in rows]

    def search(self, query: str, scopes: Optional[dict[str, str]] = None, tags: Optional[Iterable[str]] = None,
               limit: int = 5, touch: bool = True) -> list[Memory]:
        """Keyword search. A memory matches if it shares at least one word with the query (in its content,
        key or tags); an empty query matches everything. Ranking (see score()) favors more matching words,
        then importance, then recent use."""
        where, args = self._filter(scopes, tags)
        rows = [self._row(r) for r in self.db.execute(f"SELECT * FROM memories WHERE {where}", args)]
        terms = words(query)
        now = self.clock()
        scored = []
        for m in rows:
            hits = len(terms & words(f"{m.content} {m.key or ''} {' '.join(m.tags)}")) if terms else 0
            if terms and not hits:
                continue
            scored.append((self.score(hits, m, now), m.id, m))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        found = [m for _, _, m in scored[:limit]]
        if touch and found:
            self._touch([m.id for m in found])
        return found

    @staticmethod
    def score(hits: int, m: Memory, now: float) -> float:
        # A matching word (6) outweighs the whole importance range (1-5) plus recency (0-1), so relevance
        # decides first. Recency halves every 7 days since last use and only breaks importance ties.
        recency = 0.5 ** ((now - m.last_used_at) / (7 * DAY))
        return 6.0 * hits + m.importance + recency

    def _touch(self, ids: list[int]) -> None:
        self.db.execute(f"UPDATE memories SET last_used_at = ?, use_count = use_count + 1 "
                        f"WHERE id IN ({','.join('?' * len(ids))})", (self.clock(), *ids))
        self.db.commit()

    def find_duplicate(self, content: str, scope: str, namespace: str = "", threshold: float = 0.6) -> Optional[Memory]:
        """An existing memory in the same scope that says nearly the same thing, if any."""
        best, best_sim = None, threshold
        for m in self.list({scope: namespace}, limit=10_000):
            s = similarity(content, m.content)
            if s >= best_sim:
                best, best_sim = m, s
        return best

    def forget_expired(self, idle_days: float = 30, max_importance: int = 2) -> int:
        """Delete memories past expires_at, and memories with importance <= max_importance not used for
        idle_days. Importance 5 is never deleted automatically."""
        now = self.clock()
        cur = self.db.execute(
            "DELETE FROM memories WHERE importance < 5 AND "
            "((expires_at IS NOT NULL AND expires_at <= ?) OR (importance <= ? AND last_used_at < ?))",
            (now, min(max_importance, 4), now - idle_days * DAY))
        self.db.commit()
        return cur.rowcount
