import tempfile
import unittest
from pathlib import Path

from den.documents import Documents, passages


class DocumentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.docs = Documents(Path(tmp.name) / "library.sqlite3")

    def test_import_search_read_delete(self):
        data = "River inventory\r\n\r\n" + "boulder " * 95 + "\r\nThe kingfisher nests at the old bend."
        entry = self.docs.add("river.md", data)
        self.assertEqual(entry["id"], self.docs.add("river.md", data)["id"])
        self.assertEqual(len(self.docs.list()), 1)
        self.assertNotIn("text", self.docs.list()[0])
        hit, = self.docs.search('What about "kingfisher" / NEAR(*)?')
        self.assertIn("old bend", hit["text"])
        self.assertEqual((hit["id"], hit["name"], hit["line_end"]), (entry["id"], "river.md", 4))
        self.docs.delete(entry["id"])
        self.assertEqual(self.docs.search("kingfisher"), [])
        with self.assertRaises(KeyError):
            self.docs.get(entry["id"])

    def test_split_single_line_and_bound_queries(self):
        chunks = list(passages("x" * 1800))
        self.assertEqual("".join(text for _, _, text in chunks), "x" * 1800)
        self.assertTrue(all(first == last == 1 for first, last, _ in chunks))
        self.assertEqual(self.docs.search('What is in my files?'), [])
        with self.assertRaises(ValueError):
            self.docs.add("../bad", "hello")
        with self.assertRaises(ValueError):
            self.docs.add("data.txt", "x\x00y")
