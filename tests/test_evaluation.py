from concurrent.futures import ThreadPoolExecutor
import json
import tempfile

import time
import unittest
from pathlib import Path

from den.evaluation import STARTER_CASES, check_answer, starter_id
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

    def test_starters_are_opt_in_and_concurrent_additions_preserve_edits(self):
        route = self.lab.route
        self.lab.inventory = lambda: [{"name":"fixture"}, {"name":"preferred"}]
        endpoint = "/api/lab/cases/starters"
        self.assertEqual(route("GET", "/api/lab/cases")[1]["cases"], [])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: route("POST", endpoint, {"model":"preferred"}), range(4)))
        self.assertEqual(sum(response["added"] for _, response in results), 6)
        listing = route("GET", "/api/lab/cases")[1]["cases"]
        self.assertEqual(len(listing), 6)
        snapshots = {key:route("GET", "/api/lab/cases/"+starter_id(key))[1]["snapshot"] for key, _ in STARTER_CASES}
        for snapshot in snapshots.values():
            self.assertFalse(snapshot["live_retrieval"])
            for arm in snapshot["configs"]:
                self.assertEqual(arm["model"], "preferred")
                self.assertEqual(arm["seed"], 42)
                self.assertFalse(arm["memory_enabled"])
        self.assertEqual(snapshots["history"]["history"][0]["role"], "user")
        self.assertEqual(snapshots["reference"]["context"][0]["name"], "estuary-stations.md")
        self.assertEqual(snapshots["calculator"]["check_mode"], "tool")
        self.assertFalse(snapshots["conversation"]["expected"])

        path = "/api/lab/cases/" + starter_id("json")
        changed = {**snapshots["json"], "prompt":"My edited prompt"}
        with self.lab._db() as db:
            db.execute("UPDATE lab_cases SET name=?, snapshot=? WHERE id=?",
                       ("Personal JSON check", json.dumps(changed), starter_id("json")))
        self.lab.inventory = lambda: []
        self.assertEqual(route("POST", endpoint, {})[1], {"added":0, "skipped":6})
        kept = route("GET", path)[1]
        self.assertEqual((kept["name"], kept["snapshot"]["prompt"]), ("Personal JSON check","My edited prompt"))
        self.assertFalse(self.inputs)

    def test_missing_model_and_name_conflict(self):
        route = self.lab.route
        self.lab.inventory = lambda: []
        self.assertEqual(route("POST", "/api/lab/cases/starters", {})[0], 409)
        self.assertFalse(route("GET", "/api/lab/cases")[1]["cases"])
        self.lab.inventory = lambda: [{"name":"fixture"}]
        name = dict(STARTER_CASES)["conversation"]["name"]
        _, original = route("POST", "/api/lab/cases", {**self.payload, "name":name})
        _, result = route("POST", "/api/lab/cases/starters", {"model":"removed"})
        self.assertEqual((result["added"], result["skipped"], result["model"]), (5, 1, "fixture"))
        self.assertEqual(route("GET", "/api/lab/cases/"+original["id"])[1]["snapshot"]["prompt"], "Sky?")
        self.assertEqual(len(route("GET","/api/lab/cases")[1]["cases"]),6)

    def test_tool_check_uses_observed_execution(self):
        matched = {"tool":"calculator", "output":{"status":"completed"}}
        self.assertTrue(check_answer("tool","calculator",{"tool_execution":matched, "response":"220"}))
        self.assertFalse(check_answer("tool","calculator",{"response":"220"}))
        self.assertFalse(check_answer("tool","calculator",{"tool_execution":{**matched,"tool":"wiki_search"}}))
        self.assertFalse(check_answer("tool","calculator",{"tool_execution":{**matched,"output":{"status":"error"}}}))

    def test_checks(self):
        self.assertTrue(check_answer("json","",{"response":"[1, 2]"}))
        self.assertFalse(check_answer("json","",{"response":"broken"}))
        self.assertFalse(check_answer("exact","blue",{"response":"light blue"}))
        self.assertTrue(check_answer("source","Note.md",{"context":[{"name":"note.md"}]}))
