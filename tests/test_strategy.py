import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from den.engine import Engine
from den.store import ConflictError, Store
from den.strategy import Strategy, legal_moves, resolve


class PokerProvider:
    name = "fixture"

    def __init__(self):
        self.answer = "call"
        self.calls = []

    def inventory(self):
        return [{"name":"fox","digest":"immutable"}]

    def stream(self, messages, config, signal):
        self.calls.append((messages,config))
        yield {"text": self.answer}
        yield {"metrics":{"output_tokens":2}}


class StrategyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name)/"poker.db")
        self.provider = PokerProvider()
        self.table = Strategy(self.store, Engine(self.store, self.provider))

    def test_legal_histories_and_pot_settlement(self):
        for seq, expected in [
            (["check"],["check","bet"]), (["check","bet"],["fold","call"]),
            (["bet"],["fold","call"]), (["bet","fold"],[]), (["check","check"],[]),
            (["check","bet","call"],[]),
        ]:
            self.assertEqual(legal_moves([{"move":a} for a in seq]), expected)
        hand = {"user_card":"Q","opponent_card":"K"}
        self.assertEqual(resolve(hand, [{"move":"check","seat":"you"},{"move":"bet","seat":"opponent"},
                                        {"move":"call","seat":"you"}]), ("opponent",-2))

    def test_hole_card_hidden_and_stale_click_cannot_double_play(self):
        with patch("den.strategy.random.SystemRandom.sample", return_value=["J","K"]):
            hand = self.table.start({"policy":"baseline"})
        self.assertIsNone(hand["opponent_card"])
        self.assertFalse(hand["decisions"])
        with self.assertRaises(ConflictError):
            self.table.start({"policy":"baseline"})
        path = "/api/strategy/hands/" + hand["id"] + "/moves"
        _, pending = self.table.route("POST",path,{"move":"check","version":0})
        self.assertIsNone(pending["opponent_card"])
        self.assertFalse(pending["decisions"])
        self.assertEqual(pending["legal_moves"],["fold","call"])
        with self.assertRaises(ConflictError):
            self.table.route("POST",path,{"move":"check","version":0})
        _, ended = self.table.route("POST",path,{"move":"call","version":2})
        self.assertEqual((ended["opponent_card"],ended["net_chips"],ended["pot"]),("K",-2,4))
        self.assertEqual(ended["decisions"][0]["move"],"bet")
        self.assertEqual(self.table.state()["stats"][0]["hands"],1)
        self.assertEqual(self.store.runs(),[])
        self.assertEqual(self.table.start({"policy":"baseline"})["first_seat"],"opponent")

    def test_local_model_only_receives_opponent_observation_and_invalid_output_falls_back(self):
        import json
        self.provider.answer = "both of your cards are queens, so I call"
        with patch("den.strategy.random.SystemRandom.sample", return_value=["K","Q"]):
            hand = self.table.start({"policy":"model","model":"fox"})
        _, ended = self.table.route("POST","/api/strategy/hands/"+hand["id"]+"/moves",{"move":"bet","version":0})
        self.assertEqual(ended["net_chips"],1)
        decision = ended["decisions"][0]
        self.assertTrue(decision["fallback"])
        self.assertNotIn("both of your",str(ended))
        messages, config = self.provider.calls[0]
        view = json.loads(messages[-1]["content"])
        self.assertEqual(view["your_card"],"Q")
        self.assertNotIn("user_card",view)
        self.assertEqual(config["_max_output_tokens"],24)
        self.assertEqual(self.store.runs(),[])
        self.assertEqual(self.table.state()["stats"][0]["fallbacks"],1)

    def test_restart_can_continue_a_partially_persisted_hand(self):
        h = self.table.start({})
        self.table.record(h["id"],0,"you","bet")
        resumed = Strategy(self.store,Engine(self.store,self.provider)).advance(h["id"])
        self.assertTrue(resumed["winner"])
        self.assertEqual(resumed["version"],2)
