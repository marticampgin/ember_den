from pathlib import Path
import tempfile
import unittest

from den.engine import Engine
from den.store import Store


class CaptureProvider:
    name = "fixture"

    def __init__(self):
        self.calls = []

    def inventory(self):
        return [{"name":"fixture","digest":"test"}]

    def stream(self, messages, config, signal):
        self.calls.append(messages)
        yield {"text":"Answer"}
        yield {"metrics":{"input_tokens":18,"output_tokens":1}}


class ContextTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name)/"context.db")
        self.model = CaptureProvider()
        self.engine = Engine(self.store,self.model)

    def turn(self, text, mode="window", cid=None):
        events = list(self.engine.generate(text,{"model":"fixture","history_mode":mode,"context_length":1024},
                                           conversation_id=cid))
        return self.store.run(events[0]["run_id"])

    def test_recaps_older_than_32_messages_and_replays_after_deletion(self):
        first = self.turn("My telescope's callsign is Winter Finch.")
        cid = first["conversation_id"]
        for i in range(18):
            self.turn(f"Today I made batch {i} of coffee and measured its brewing time.",cid=cid)
        window = self.turn("What was my telescope callsign?",cid=cid)
        self.assertFalse([x for x in window["context"] if x["kind"]=="recap"])
        self.assertGreaterEqual(window["context_budget"]["history_omitted"],6)
        self.assertTrue(any(x.get("run_id")==first["id"] for x in window["recap_candidates"]))
        recap = self.turn("What was my telescope callsign?",mode="recap",cid=cid)
        selected = [x for x in recap["context"] if x["kind"]=="recap"]
        self.assertTrue(any(x["run_id"]==first["id"] and "Winter Finch" in x["text"] for x in selected))
        self.assertEqual(selected[0]["conversation_id"],cid)
        self.assertIn("reference data",self.model.calls[-1][0]["content"])
        metrics = recap["context_budget"]
        self.assertEqual(metrics["history_kept"]+metrics["history_omitted"],metrics["history_total"])
        self.assertLessEqual(metrics["estimated_input_bytes"],metrics["input_byte_budget"])
        observed = self.model.calls[-1]
        self.store.delete_conversation(cid)
        replay = self.engine.run_config(recap["prompt"],recap["config"],context=recap["context"],history=recap["history"])
        self.assertEqual(replay["context"],recap["context"])
        self.assertEqual(self.model.calls[-1],observed)
        bypass = self.engine.run_config(recap["prompt"],{**recap["config"],"history_mode":"window"},
                                        context=recap["context"], history=recap["history"])
        self.assertFalse(any(ref["kind"]=="recap" for ref in bypass["context"]))

    def test_oversize_prompt_fails_before_inference_and_preserves_input(self):
        prompt = "界" * 2000
        run = self.turn(prompt)
        self.assertEqual(run["status"],"error")
        self.assertFalse(self.model.calls)
        self.assertIn("context budget",run["error"])
        self.assertGreater(run["context_budget"]["base_input_bytes"],run["context_budget"]["input_byte_budget"])
        self.assertEqual(self.store.conversation(run["conversation_id"])["messages"][0]["content"],prompt)
        allowed = self.engine.run_config(prompt,{"model":"fixture","context_length":4096})
        self.assertEqual(allowed["status"],"completed")

    def test_large_unicode_references_and_legacy_history_are_bounded(self):
        references = [{"id":"doc","kind":"document","text":'🌲"\\\n' * 2000}]
        history = [{"role":"user","content":"Old telescope code is Copper Fox."},
                   {"role":"assistant","content":"Fine."}]
        history += [{"role":"user","content":"Other details " * 50},
                    {"role":"assistant","content":"Acknowledged."}]*5
        r = self.engine.run_config("What is the telescope code?",{"model":"fixture","history_mode":"recap","context_length":1024},
                                   context=references,history=history)
        budget = r["context_budget"]
        self.assertLessEqual(budget["estimated_input_bytes"],budget["input_byte_budget"])
        self.assertGreater(budget["history_omitted"],0)
        self.assertLess(len(r["context"][0]["text"]),len(references[0]["text"]))
        self.assertTrue(any(x["kind"]=="recap" for x in r["context"]))
