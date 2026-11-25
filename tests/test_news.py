import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch

from den.news import FEEDS, LIMIT, PublisherNews, WebSearch, parse_feed, public_host
from den.tools import Tools


def feed_body(feed):
    parts = []
    for index, (age, title) in enumerate([(2,"Salmon migration"), (3,"River levels"), (12,"Old salmon report")]):
        published = format_datetime(datetime.now(timezone.utc) - timedelta(days=age))
        parts.append(f"<item><title>{title}</title><link>https://{feed.article_hosts[0]}/article/{index}</link>"
                     f"<description>River station report</description><pubDate>{published}</pubDate></item>")
    return ("<rss><channel>" + "".join(parts) + "</channel></rss>").encode()


class NewsTest(unittest.TestCase):
    def test_fixed_feeds_filtered_locally_cached_and_dated(self):
        feeds = {feed.path:feed for feed in FEEDS}
        news = PublisherNews()
        with patch("den.news._read", side_effect=lambda _, path, source:feed_body(feeds[path])) as read:
            latest = news.search("headlines")
            matched = news.search("salmon")
            none = news.search("unmatched")
        self.assertEqual(read.call_count,3)
        self.assertEqual({call.args[1] for call in read.call_args_list}, set(feeds))
        self.assertEqual(len(latest["context"]),5)
        self.assertEqual({hit["publisher"] for hit in matched["context"]},{f.name for f in FEEDS})
        self.assertTrue(all(hit["name"]=="Salmon migration" and hit["published_at"] for hit in matched["context"]))
        self.assertFalse(none["context"])
        self.assertIn("0 matching",none["response"])

    def test_partial_and_total_outage_and_invalid_xml(self):
        def one_feed(feed):
            if feed == FEEDS[0]:
                return parse_feed(feed,feed_body(feed))
            raise ValueError("Unavailable")
        news=PublisherNews()
        with patch.object(news,"_feed",side_effect=one_feed):
            result=news.search("headlines")
        self.assertIn("Unavailable: The Guardian, NPR.",result["notice"])
        with patch.object(news,"_feed",side_effect=ValueError("Offline")), self.assertRaisesRegex(ValueError,"None of"):
            news.search("headlines")
        for raw in (b"<invalid", b"<!DOCTYPE rss><rss/>", b"<wrong/>"):
            with self.assertRaises(ValueError):
                parse_feed(FEEDS[0],raw)
        self.assertEqual(parse_feed(FEEDS[0],feed_body(FEEDS[1])),[])

    def test_search_has_bounded_inputs_outputs_and_no_followup_reads(self):
        query = "what is 2+2 & more?"
        urls = ["https://127.0.0.1/a", "javascript:foo", "https://user:pass@host.com",
                "https://a.corp.local/path", "https://host.com:9000", "http://source.org",
                "https://host.com\\@else.org","https://127.1", "https://x.org/path\ninjected"]
        self.assertTrue(all(not public_host(url) for url in urls))
        safe = [f"https://source{i}.org/results" for i in range(8)]
        payload = {"results":[{"title":"<b>Search</b> hit","url":url,"content":"a &amp; b"}
                              for url in urls + [safe[0]] + safe]}
        with patch("den.news._read",return_value=json.dumps(payload).encode()) as read:
            result=WebSearch("").search(query)
        self.assertEqual(read.call_count,1)
        self.assertEqual(read.call_args.args[0].host, "api.mwmbl.org")
        self.assertEqual(read.call_args.args[1],"/api/v2/search/?q=what+is+2%2B2+%26+more%3F")
        self.assertEqual([hit["url"] for hit in result["context"]], safe[:5])
        self.assertEqual(result["context"][0]["text"], "a & b")
        self.assertEqual(result["context"][0]["name"], "Search hit")

    def test_searxng_stays_local_on_error(self):
        response=Mock(status=403)
        with patch("den.news.http.client.HTTPConnection") as local, patch("den.news.http.client.HTTPSConnection") as remote:
            local.return_value.getresponse.return_value=response
            with self.assertRaisesRegex(ValueError, "JSON output"):
                WebSearch(9988).search("sample")
            local.assert_called_once_with("127.0.0.1",9988,timeout=8)
            self.assertIn("format=json", local.return_value.request.call_args.args[1])
            remote.assert_not_called()
        with self.assertRaisesRegex(ValueError,"local TCP port"):
            WebSearch("localhost:9988").search("sample")
        with patch("den.news.http.client.HTTPSConnection") as remote:
            response = remote.return_value.getresponse.return_value
            response.status=200;response.read.return_value=b"a"*(LIMIT+1)
            with self.assertRaisesRegex(ValueError,"too much data"): WebSearch("").search("test")
            response.read.assert_called_once_with(LIMIT+1)

    def test_commands_tooldesk_and_trace_contract(self):
        with TemporaryDirectory() as root:
            search=WebSearch(9988)
            tools=Tools(root,search_source=search)
            self.assertEqual(tools.parse("/web fox")[0].name, "wiki_search")
            self.assertEqual(tools.parse("Latest headlines")[1],{"query":"headlines"})
            self.assertEqual(tools.parse("/news")[1],{"query":"headlines"})
            self.assertEqual(tools.parse("Search news for climate")[0].name,"news_search")
            self.assertEqual(tools.state()["search_provider"], "SearXNG")
            with patch("den.news._read",return_value=b'{"results":[]}') as read:
                result=tools.chat("Search the web for Python")
                self.assertEqual(result["tool"], "web_search")
                self.assertIn("SearXNG",result["events"][0]["detail"])
                self.assertEqual(tools.chat("/search password=privatePass")["status"],"error")
                self.assertEqual(read.call_count,1)
                status, result=tools.route("POST","/api/tools/run",{"tool":"web_search","arguments":{"query":"Python"}})
                self.assertEqual(status,200)
                self.assertIn("0 public HTTPS",result["response"])

    def test_real_loopback_json_search_fixture(self):
        seen=[]
        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.end_headers()
                self.wfile.write(b'{"results":[{"title":"Python library","url":"https://docs.python.org/3/","content":"Library reference"}]}')

            def log_message(self,*args):
                pass

        try:
            server=HTTPServer(("127.0.0.1",0), Fixture)
        except OSError as exc:
            self.skipTest(f"Loopback sockets unavailable: {exc}")
        with server, TemporaryDirectory() as root:
            worker=Thread(target=server.serve_forever,daemon=True)
            worker.start()
            try:
                try:
                    with socket.create_connection(server.server_address,timeout=2):
                        pass
                except OSError as exc:
                    self.skipTest(f"Loopback connections unavailable: {exc}")
                with patch.dict("os.environ", {"EMBER_SEARXNG_PORT":str(server.server_port)}):
                    tools=Tools(root)
                    result=tools.chat("/search Python standard library")
                self.assertEqual(result["status"],"completed",result["response"])
                self.assertEqual(result["context"][0]["provider"],"SearXNG")
                self.assertEqual(result["context"][0]["publisher"],"docs.python.org")
                self.assertEqual(len(seen),1)
                self.assertIn("/search?q=Python+standard+library&format=json", seen[0])
            finally:
                server.shutdown()
                worker.join(2)
