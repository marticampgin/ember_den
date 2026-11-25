import tempfile
import time
import unittest
from pathlib import Path

from den.evaluation import check_answer
from den.laboratory import Laboratory


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.inputs = []
        def infer(prompt, config, **kwargs):
            self.inputs.append(kwargs["context"])
            return {"id":"run1", "status":"completed", "response":"Blue",
                    "context":kwargs["context"] if kwargs["context"] is not None else [{"text":"Retrieved"}]}
        self.lab = Laboratory(Path(directory.name)/"test.db", infer, lambda:[{"name":"fixture"}])
        self.payload = {"name":"sky", "prompt":"Sky?", "configs":[{"model":"fixture"}]*2,
                        "expected":"blue","check_mode":"exact","live_retrieval":True}

    def wait(self, read):
        for _ in range(300):
            value = read()
            if value.get("state",value.get("status")) != "running":
                return value
            time.sleep(.01)
        self.fail("Timed out waiting for test suite")

    def test_profiles_and_suite_with_frozen_replay(self):
        route = self.lab.route
        _, created = route("POST","/api/lab/profiles",{"name":"Compact","config":{"model":"fixture"}})
        self.assertEqual(route("GET","/api/lab/profiles")[1]["profiles"][0]["config"]["model"],"fixture")
        route("DELETE","/api/lab/profiles/"+created["id"])
        self.assertFalse(route("GET","/api/lab/profiles")[1]["profiles"])
        with self.assertRaises(ValueError):
            route("POST","/api/lab/suites",{})
        route("POST","/api/lab/cases",self.payload)
        self.assertEqual(route("POST","/api/lab/suites",{})[0],202)
        suite = self.wait(lambda:route("GET","/api/lab/suites")[1]["suites"][0])
        self.assertEqual(suite["cases"][0]["checks"],[True,True])
        identifier = suite["cases"][0]["experiment_id"]
        self.assertEqual(self.inputs,[None,None])
        _, replay = route("POST","/api/lab/experiments/"+identifier+"/replay",{})
        self.wait(lambda:self.lab.experiment(replay["id"]))
        self.assertEqual(self.inputs[-2:],[[{"text":"Retrieved"}]]*2)
        route("POST","/api/lab/reviews/"+identifier,{"preference":"Tie"})
        self.assertEqual(self.lab.experiment(identifier)["preference"],"Tie")

    def test_checks(self):
        self.assertTrue(check_answer("json","",{"response":"[1, 2]"}))
        self.assertFalse(check_answer("json","",{"response":"broken"}))
        self.assertFalse(check_answer("exact","blue",{"response":"light blue"}))
        self.assertTrue(check_answer("source","Note.md",{"context":[{"name":"note.md"}]}))
