"""A three-card Kuhn poker experiment. All chips are simulated."""

import json
import random
import re
import threading
import time

from .cancel import Cancellation
from .engine import choose_model, validate_config
from .provider import ProviderError
from .store import ConflictError, new_id, utcnow


RANKS = ("J", "Q", "K")


def next_seat(first, moves):
    return first if len(moves) % 2 == 0 else ("opponent" if first == "you" else "you")


def legal_moves(moves):
    history = [event["move"] for event in moves]
    if history and (history[-1] in ("call", "fold") or history == ["check", "check"]):
        return []
    return ["fold", "call"] if history and history[-1] == "bet" else ["check", "bet"]


def baseline(card, legal):
    if "call" in legal:
        return "call" if card == "K" else "fold"
    return "bet" if card == "K" else "check"


def resolve(hand, moves):
    if legal_moves(moves):
        return None, 0
    if moves[-1]["move"] == "fold":
        winner = "opponent" if moves[-1]["seat"] == "you" else "you"
    else:
        winner = "you" if RANKS.index(hand["user_card"]) > RANKS.index(hand["opponent_card"]) else "opponent"
    chips = 2 if moves[-1]["move"] == "call" else 1
    return winner, chips if winner == "you" else -chips


class Strategy:
    def __init__(self, store, engine):
        self.store = store
        self.engine = engine
        self._opponent_lock = threading.Lock()
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS strategy_hands (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    policy TEXT NOT NULL, model TEXT NOT NULL, first_seat TEXT NOT NULL,
                    user_card TEXT NOT NULL, opponent_card TEXT NOT NULL, moves TEXT NOT NULL,
                    decisions TEXT NOT NULL, winner TEXT, net_chips INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS strategy_active ON strategy_hands((1)) WHERE winner IS NULL;
            """)

    def _hand(self, identifier):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM strategy_hands WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError("Hand not found.")
        return dict(row)

    @staticmethod
    def public(hand):
        moves = json.loads(hand["moves"])
        ended = hand["winner"] is not None
        turn = None if ended else next_seat(hand["first_seat"], moves)
        return {
            "id":hand["id"], "created_at":hand["created_at"], "policy":hand["policy"], "model":hand["model"],
            "user_card":hand["user_card"], "opponent_card":hand["opponent_card"] if ended else None,
            "first_seat":hand["first_seat"], "turn":turn, "moves":moves, "version":len(moves),
            "legal_moves":legal_moves(moves) if turn == "you" else [], "winner":hand["winner"],
            "net_chips":hand["net_chips"], "pot":2 + sum(m["move"] in ("call", "bet") for m in moves),
            "decisions":json.loads(hand["decisions"]) if ended else [],
        }

    def state(self):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM strategy_hands ORDER BY rowid DESC LIMIT 1").fetchone()
            recent = db.execute("""SELECT id,policy,model,updated_at,winner,net_chips FROM strategy_hands
                                   WHERE winner IS NOT NULL ORDER BY rowid DESC LIMIT 8""").fetchall()
            stats = db.execute("""SELECT policy, model, COUNT(*) AS hands, SUM(winner='you') AS wins,
                    SUM(net_chips) AS net_chips FROM strategy_hands WHERE winner IS NOT NULL
                    GROUP BY policy,model ORDER BY policy,model""").fetchall()
            timing = db.execute("""SELECT policy, model, COUNT(*) AS decisions,
                    ROUND(AVG(json_extract(d.value,'$.elapsed_ms')),1) AS mean_ms,
                    SUM(json_extract(d.value,'$.fallback') IS NOT NULL) AS fallbacks
                    FROM strategy_hands h, json_each(h.decisions) d WHERE h.winner IS NOT NULL
                    GROUP BY policy,model""").fetchall()
        timing_by_policy = {(r["policy"],r["model"]):dict(r) for r in timing}
        return {"hand":self.public(row) if row else None, "recent":[dict(r) for r in recent],
                "stats":[{**dict(r), **timing_by_policy.get((r["policy"],r["model"]), {})} for r in stats]}

    def start(self, body):
        policy = body.get("policy", "baseline")
        if policy not in ("baseline","model"):
            raise ValueError("Choose a baseline or local-model opponent.")
        model = ""
        if policy == "model":
            models = self.engine.provider.inventory()
            model = body.get("model") or choose_model(models)
            if not isinstance(model,str) or model not in {m["name"] for m in models}:
                raise ValueError("Select an installed local chat model.")
        identifier, date = new_id(), utcnow()
        cards = random.SystemRandom().sample(RANKS, 2)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM strategy_hands WHERE winner IS NULL").fetchone():
                raise ConflictError("Finish the current hand before dealing again.")
            count = db.execute("SELECT count(*) FROM strategy_hands WHERE policy=? AND model=?", (policy,model)).fetchone()[0]
            first = "you" if count % 2 == 0 else "opponent"
            db.execute("""INSERT INTO strategy_hands VALUES (?,?,?,?,?,?,?,?,'[]','[]',NULL,0)""",
                       (identifier,date,date,policy,model,first,*cards))
        return self.advance(identifier)

    def record(self, identifier, version, seat, action, decision=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            hand = db.execute("SELECT * FROM strategy_hands WHERE id=?", (identifier,)).fetchone()
            if not hand:
                raise KeyError("Hand not found.")
            moves, decisions = json.loads(hand["moves"]), json.loads(hand["decisions"])
            if version != len(moves) or hand["winner"] or next_seat(hand["first_seat"], moves) != seat:
                raise ConflictError("This hand has changed. Refresh the table.")
            if action not in legal_moves(moves):
                raise ValueError("That move is unavailable in this position.")
            moves.append({"seat":seat,"move":action})
            if decision:
                decisions.append(decision)
            winner, net = resolve(hand, moves)
            db.execute("""UPDATE strategy_hands SET updated_at=?,moves=?,decisions=?,winner=?,net_chips=? WHERE id=?""",
                       (utcnow(),json.dumps(moves),json.dumps(decisions),winner,net,identifier))

    def opponent(self, hand, moves):
        started = time.monotonic()
        legal = legal_moves(moves)
        fallback = baseline(hand["opponent_card"],legal)
        decision = {"policy":hand["policy"],"legal":legal,"fallback":None,"metrics":{}}
        action = fallback
        if hand["policy"] == "model":
            name = hand["model"]
            try:
                models = {m["name"]:m for m in self.engine.provider.inventory()}
                if name not in models:
                    raise ProviderError("Selected model is unavailable.")
                decision["model_digest"] = models[name].get("digest")
                # No player card, other private sessions, general trace, or persistent conversation.
                observation = {"your_card": hand["opponent_card"], "first_player": hand["first_seat"],
                               "history": moves, "legal_actions":legal}
                messages = [{"role":"system","content":(
                    "Play one move of Kuhn poker as 'opponent'. J < Q < K. Both players ante one chip, "
                    "and one bet of one chip is allowed. Maximize simulated net chips. "
                    "Reply with exactly one of the legal action words.")},
                            {"role":"user","content":json.dumps(observation)}]
                config = validate_config({"model":name,"temperature":0,"context_length":2048,"seed":73})
                config["_max_output_tokens"] = 24
                signal = Cancellation()
                chunks = []
                with signal.slot(self.engine.model_slot):
                    for packet in self.engine.provider.stream(messages,config,signal):
                        if "metrics" in packet:
                            decision["metrics"] = packet["metrics"]
                        if packet.get("text"):
                            chunks.append(packet["text"])
                proposed = "".join(chunks).strip().lower()
                if proposed not in legal:
                    raise ValueError("Model returned an invalid move.")
                action = proposed
            except (ValueError, ProviderError):
                decision["fallback"] = "Invalid or unavailable model response. Applied the baseline policy."
        decision.update(move=action, elapsed_ms=round((time.monotonic()-started)*1000,2))
        return action, decision

    def advance(self, identifier):
        # Concurrent tabs can observe the hand while the model runs outside a database transaction.
        if self._opponent_lock.acquire(blocking=False):
            try:
                hand = self._hand(identifier)
                moves = json.loads(hand["moves"])
                if hand["winner"] is None and next_seat(hand["first_seat"], moves) == "opponent":
                    action, decision = self.opponent(hand,moves)
                    self.record(identifier,len(moves),"opponent",action,decision)
            finally:
                self._opponent_lock.release()
        return self.public(self._hand(identifier))

    def route(self, method, path, body):
        if path == "/api/strategy/state" and method == "GET":
            return 200, self.state()
        if path == "/api/strategy/hands" and method == "POST":
            return 201, self.start(body)
        match = re.fullmatch(r"/api/strategy/hands/([a-f0-9]{32})(?:/(moves|continue))?", path)
        if not match:
            return None
        identifier, action = match.groups()
        if method == "GET" and not action:
            return 200, self.public(self._hand(identifier))
        if method == "POST" and action == "moves":
            version = body.get("version")
            if type(version) is not int:
                raise ValueError("Provide the version of the hand being played.")
            self.record(identifier,version,"you",body.get("move"))
            return 200, self.advance(identifier)
        if method == "POST" and action == "continue":
            return 200, self.advance(identifier)
        return None
