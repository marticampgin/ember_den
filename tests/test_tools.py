import concurrent.futures
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from den.tools import PublicSourceError, Tools


class PublicFixture:
    def __init__(self):
        self.calls=[]
    def search(self,query):
        self.calls.append(("search",query))
        if query=="offline": raise PublicSourceError("Service unavailable.")
        return [{"kind":"web","id":"17","name":"Red fox","text":"A wild fox.","url":"https://en.wikipedia.org/?curid=17"}]
    def read(self,page_id):
        self.calls.append(("read",page_id))
        return {"kind":"web","id":"17","name":"Red fox","text":"The red fox lives across Eurasia.","url":"https://en.wikipedia.org/?curid=17"}


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.data=tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        self.source=PublicFixture()
        self.tools=Tools(self.data.name,self.source)
        self.exports=Path(self.data.name)/"exports"

    def test_multistep_lookup_and_errors(self):
        result=self.tools.chat("/research red fox")
        self.assertEqual(self.source.calls,[("search","red fox"),("read","17")])
        self.assertIn("Eurasia",result["response"])
        self.assertEqual(len(result["events"]),2)
        self.assertEqual(result["context"][0]["kind"],"web")
        self.assertIsNone(self.tools.chat("Could you help me?"))
        expected=self.tools.chat("/web offline")
        self.assertEqual(expected["status"],"error")
        self.assertEqual(self.tools.state()["activity"][0]["status"],"error")
        self.assertEqual(self.tools.chat("/web api_key=secret1234")["status"],"error")
        self.assertNotIn("secret1234",str(self.tools.state()["activity"]))
        self.assertNotIn(("search","api_key=secret1234"),self.source.calls)

    def test_no_file_before_explicit_approval_and_idempotent_retries(self):
        result=self.tools.chat("/note ../outside | My body")
        self.assertEqual(result["status"],"completed")
        self.assertFalse(self.exports.exists())
        proposal=result["proposal_id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            states=list(pool.map(lambda _:self.tools.decide(proposal,"approve"),range(2)))
        self.assertEqual([status for status, data in states],[200,200])
        files=list(self.exports.iterdir())
        self.assertEqual(len(files),1)
        self.assertEqual(files[0].read_text("utf8"),"../outside\n\nMy body\n")
        self.assertEqual(len([r for r in self.tools.state()["activity"] if r["permission"]=="explicit approve"]),1)
        self.assertEqual(self.tools.decide(proposal,"decline")[0],409)

    def test_explicit_natural_intents_and_bounded_weather(self):
        self.assertFalse(self.tools.accepts("Explain how to search Wikipedia for a fox."))
        self.assertTrue(self.tools.accepts("Research Wikipedia for Vulpes vulpes"))
        self.assertEqual(self.tools.chat("Search Wikipedia for red fox")["tool"],"wiki_search")
        self.assertIn("proposal_id",self.tools.chat("Draft a note titled Trail journal with the text Fox sighting."))
        self.assertFalse(self.exports.exists())
        city={"results":[{"name":"Berlin","admin1":"Berlin","country_code":"DE","latitude":52.52,"longitude":13.41}]}
        weather={"timezone":"Europe/Berlin","current":{"time":"2026-11-25T12:00","temperature_2m":4},
                 "daily":{"time":["2026-11-25","2026-11-26","2026-11-27"],"temperature_2m_min":[1,2,3],"temperature_2m_max":[6,7,8]}}
        with patch("den.tools.public_json",side_effect=[city,weather]) as fetch:
            result=self.tools.chat("What's the weather like in Berlin, DE?")
        self.assertIn("2026-11-27",result["response"])
        self.assertEqual(result["context"][0]["kind"],"web")
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(fetch.call_args_list[0].args[2]["countryCode"],"DE")
        with patch("den.tools.public_json",return_value={"results":[]}) as fetch:
            self.assertEqual(self.tools.chat("/weather imaginary city")["status"],"error")
            self.assertEqual(fetch.call_count,1)
        with patch("den.tools.public_json") as fetch:
            self.assertEqual(self.tools.chat("/weather password=secret123")["status"],"error")
            fetch.assert_not_called()

    def test_decline_and_collision_preserve_files(self):
        proposal=self.tools.chat("/note Keep | Proposed content")["proposal_id"]
        self.tools.decide(proposal,"decline")
        self.assertFalse(self.exports.exists())
        self.assertEqual(self.tools.decide(proposal,"approve")[0],409)
        second=self.tools.chat("/note Keep | Proposed content")["proposal_id"]
        name=self.tools.state()["pending"][0]["filename"]
        self.exports.mkdir()
        target=self.exports/name
        target.write_text("User-owned file",encoding="utf8")
        self.assertEqual(self.tools.decide(second,"approve")[0],409)
        self.assertEqual(target.read_text("utf8"),"User-owned file")
        self.assertEqual(len(self.tools.state()["pending"]),1)

    def test_verifiable_calendar_and_typed_registry(self):
        self.assertEqual(self.tools.chat("/event 2027-02-29 | Invalid")["status"],"error")
        title="Fox, compass; " + "É"*50
        result=self.tools.chat("/event 2028-02-29 | "+title)
        _,out=self.tools.route("POST","/api/tools/proposals/"+result["proposal_id"]+"/approve",{})
        data=(self.exports/out["filename"]).read_bytes()
        self.assertIn(b"DTSTART;VALUE=DATE:20280229\r\n",data)
        self.assertIn(b"DTEND;VALUE=DATE:20280301\r\n",data)
        self.assertIn(b"Fox\\, compass\\;",data)
        self.assertTrue(all(len(line)<=75 for line in data.split(b"\r\n")))
        self.assertTrue(all(definition["schema"]["type"]=="object" for definition in self.tools.state()["tools"]))
        with self.assertRaises(ValueError):
            self.tools.run("export_note",{"title":"a","body":"b","path":"C:/somewhere"})

