"""A user-curated, line-addressable text library with local full-text retrieval."""

import hashlib
import re

from .capabilities import STOPWORDS
from .store import Store, new_id, utcnow


MAX_TEXT = 120_000
CHUNK_SIZE = 680
QUERY_NOISE = STOPWORDS | set(
    "about any could document documents file files find have notes please say says tell that "
    "their them this using was we were which will would according imported summarize summary".split()
)


def passages(text):
    """Keep line references meaningful, including for single-line exports."""
    parts = []
    size = 0
    first = last = 1
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        for offset in range(0, len(line), CHUNK_SIZE):
            fragment = line[offset:offset + CHUNK_SIZE]
            if parts and size + len(fragment) > CHUNK_SIZE:
                yield first, last, "\n".join(parts)
                parts, size = [], 0
            if not parts:
                first = line_no
            parts.append(fragment)
            last = line_no
            size += len(fragment) + 1
    if parts:
        yield first, last, "\n".join(parts)


class Documents:
    def __init__(self, path):
        self.store = Store(path)
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL,
                    digest TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(name, digest)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_index USING fts5(
                    name, text, id UNINDEXED, chunk UNINDEXED,
                    line_start UNINDEXED, line_end UNINDEXED, tokenize='unicode61'
                );
            """)

    def add(self, name, text):
        if (not isinstance(name, str) or not name.strip() or len(name) > 180
                or any(ord(c) < 32 for c in name) or "/" in name or "\\" in name):
            raise ValueError("Choose a file with a plain filename of up to 180 characters.")
        if not isinstance(text, str) or not text.strip() or "\x00" in text or len(text) > MAX_TEXT:
            raise ValueError("Choose a nonempty text file of up to 120,000 characters.")
        name = name.strip()
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM documents WHERE name=? AND digest=?", (name, digest)).fetchone()
            if existing:
                identifier = existing["id"]
            else:
                identifier = new_id()
                db.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (identifier, name, text, digest, utcnow()))
                db.executemany("INSERT INTO document_index VALUES (?,?,?,?,?,?)",
                               ((name, snippet, identifier, i, first, last)
                                for i, (first, last, snippet) in enumerate(passages(text), 1)))
        return self.get(identifier)

    def list(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, name, length(text) AS characters, created_at FROM documents ORDER BY created_at DESC, rowid DESC"
            )]

    def get(self, identifier):
        with self.store.connect() as db:
            row = db.execute("SELECT id, name, text, length(text) AS characters, created_at FROM documents WHERE id=?",
                             (identifier,)).fetchone()
        if row is None:
            raise KeyError("Document not found.")
        return dict(row)

    def delete(self, identifier):
        with self.store.connect() as db:
            if not db.execute("DELETE FROM documents WHERE id=?", (identifier,)).rowcount:
                raise KeyError("Document not found.")
            db.execute("DELETE FROM document_index WHERE id=?", (identifier,))

    def search(self, prompt, limit=3):
        words = dict.fromkeys(w for w in re.findall(r"[^\W_]+", prompt.casefold()) if w not in QUERY_NOISE)
        if not words:
            return []
        # Only tokenizer words enter the FTS expression. User punctuation is never an operator.
        query = " OR ".join(f'"{w}"' for w in list(words)[:32])
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT id, name, text, chunk, line_start, line_end FROM document_index
                WHERE document_index MATCH ? ORDER BY bm25(document_index, 1.6, 1.0), rowid LIMIT ?
            """, (query, min(8, max(0, limit))))]
