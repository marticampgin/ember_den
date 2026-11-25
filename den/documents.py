"""A user-curated, line-addressable text library with local full-text retrieval."""

import hashlib
import heapq
import math
import re
import struct

from .capabilities import STOPWORDS
from .provider import ProviderError
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
                CREATE TABLE IF NOT EXISTS document_vectors (
                    id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk INTEGER NOT NULL, model_key TEXT NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY (model_key,id,chunk)
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

    def ranked(self, prompt, mode, provider, model, signal, device, limit=3):
        """Lazily index exact model revisions. Each committed batch survives cancellation."""
        name = model["name"]
        model_key = name + ":" + (model.get("digest") or "unversioned")
        with self.store.connect() as db:
            latest = db.execute("SELECT max(rowid) FROM document_index").fetchone()[0]
        metadata = {"mode": mode, "model": name, "digest": model.get("digest"), "new_vectors": 0}
        if latest is None:
            return [], metadata
        while True:
            signal.check()
            with self.store.connect() as db:
                rows = db.execute("""SELECT i.id,i.chunk,i.name,i.text FROM document_index AS i
                    LEFT JOIN document_vectors AS v ON v.model_key=? AND v.id=i.id AND v.chunk=i.chunk
                    WHERE i.rowid<=? AND v.id IS NULL ORDER BY i.rowid LIMIT 16""",
                                  (model_key, latest)).fetchall()
            if not rows:
                break
            raw = provider.embed(name, [r["name"] + "\n" + r["text"] for r in rows], signal, device)
            vectors = [unit_vector(v) for v in raw]
            if len(vectors) != len(rows):
                raise ProviderError("Embedding batch size changed unexpectedly.")
            with self.store.connect() as db:
                for row, vector in zip(rows, vectors):
                    # A document may have been deleted while its embeddings were being calculated.
                    cursor = db.execute("""INSERT OR IGNORE INTO document_vectors
                        SELECT id,?,?,? FROM documents WHERE id=?""",
                                        (row["chunk"], model_key, struct.pack(f"<{len(vector)}f", *vector), row["id"]))
                    metadata["new_vectors"] += cursor.rowcount
        query = prompt
        if "qwen3" in name.lower():
            query = "Instruct: Retrieve passages that answer the user's question.\nQuery: " + query
        q = unit_vector(provider.embed(name, [query], signal, device)[0])
        width = 4 * len(q)
        hits = []
        with self.store.connect() as db:
            rows = db.execute("""SELECT i.id,i.chunk,i.rowid AS ord,v.vector FROM document_index AS i
                JOIN document_vectors AS v ON v.id=i.id AND v.chunk=i.chunk
                WHERE v.model_key=? AND i.rowid<=?""", (model_key, latest))
            for i, row in enumerate(rows):
                if i % 64 == 0:
                    signal.check()
                blob = row["vector"]
                if len(blob) != width:
                    raise ProviderError("Embedding dimensions changed. Re-select an embedding model.")
                score = sum(a*b for a, b in zip(q, struct.unpack(f"<{len(q)}f", blob)))
                if score >= 0.25:
                    heapq.heappush(hits, (score, -row["ord"], row["id"], row["chunk"]))
                    if len(hits) > max(8, limit):
                        heapq.heappop(hits)
            candidates = {}
            for rank, (score, neg_rowid, ident, chunk) in enumerate(sorted(hits, reverse=True), 1):
                row = db.execute("""SELECT id,name,text,chunk,line_start,line_end FROM document_index
                                   WHERE rowid=? AND id=?""", (-neg_rowid, ident)).fetchone()
                if row:
                    candidates[(ident, chunk)] = (0.6/(1+rank), {**dict(row), "similarity": round(score, 4)})
        if mode == "hybrid":
            # A strong single-channel hit should survive a crowd of weak dual-channel matches.
            metadata["fusion"] = "reciprocal rank (k=1, semantic=0.6, keyword=0.4)"
            for rank, row in enumerate(self.search(prompt, limit=8), 1):
                key = (row["id"], row["chunk"])
                prior, item = candidates.get(key, (0, row))
                candidates[key] = (prior + 0.4/(1+rank), item)
        ordered = sorted(candidates.values(), key=lambda item: item[0], reverse=True)
        return [item for _, item in ordered[:limit]], metadata


def unit_vector(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 16384:
        raise ProviderError("The embedding model returned an invalid vector.")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
        raise ProviderError("The embedding model returned non-finite values.")
    norm = math.sqrt(sum(x*x for x in values))
    if norm <= 0 or not math.isfinite(norm):
        raise ProviderError("The embedding model returned a zero or non-finite vector.")
    return [x / norm for x in values]
