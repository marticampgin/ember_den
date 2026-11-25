"""Searchable previous turns, derived transactionally from completed companion runs."""

import re

from .documents import QUERY_NOISE


class Episodes:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='episode_index'").fetchone():
                return
            db.execute("""CREATE VIRTUAL TABLE episode_index USING fts5(
                prompt, answer, run_id UNINDEXED, conversation_id UNINDEXED, tokenize='unicode61'
            )""")
            select = """SELECT prompt, json_extract(snapshot,'$.response'), id, conversation_id FROM runs
                        WHERE source='chat' AND status='completed' AND json_extract(snapshot,'$.route')='chat'"""
            db.execute("INSERT INTO episode_index " + select)
            db.execute(f"""CREATE TRIGGER episode_on_complete AFTER UPDATE OF status ON runs
                WHEN NEW.status='completed' AND OLD.status<>'completed'
                BEGIN INSERT INTO episode_index {select} AND id=NEW.id; END""")
            db.execute("""CREATE TRIGGER episode_on_remove AFTER DELETE ON runs BEGIN
                DELETE FROM episode_index WHERE run_id=OLD.id; END""")

    def search(self, prompt, conversation_id, limit=2):
        words = dict.fromkeys(w for w in re.findall(r"[^\W_]+", prompt.casefold())
                              if w not in QUERY_NOISE and len(w) > 1)
        query = " OR ".join(f'"{w}"' for w in list(words)[:32])
        columns = """e.run_id AS id, e.run_id, e.conversation_id, c.title AS name,
                     substr(e.prompt,1,320) AS question"""
        source = """FROM episode_index AS e JOIN conversations AS c ON c.id=e.conversation_id
                    WHERE e.conversation_id<>?"""
        with self.store.connect() as db:
            rows = []
            if query:
                rows = db.execute(f"""SELECT {columns}, snippet(episode_index,1,'','',' … ',48) AS answer
                    {source} AND episode_index MATCH ? ORDER BY bm25(episode_index,1.6,1), e.rowid DESC LIMIT ?""",
                                  (conversation_id or "", query, limit)).fetchall()
            if not rows and re.search(r"\b(?:previous|last|earlier|recent) (?:chat|conversation|discussion)", prompt, re.I):
                rows = db.execute(f"""SELECT {columns}, substr(e.answer,1,500) AS answer
                                     {source} ORDER BY e.rowid DESC LIMIT ?""",
                                  (conversation_id or "", limit)).fetchall()
        return [{"id": r["id"], "run_id": r["run_id"], "kind": "episode", "conversation_id": r["conversation_id"],
                 "name": r["name"], "text": f"User: {r['question']}\nEmber: {r['answer']}"[:1400]} for r in rows]
