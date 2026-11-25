"""Read fixed publisher feeds and a single configured search API. Result pages are never requested."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
import http.client
import json
import os
import re
import threading
import time
from urllib.parse import urlencode, urlsplit
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class Feed:
    name: str
    host: str
    path: str
    article_hosts: tuple


FEEDS = (
    Feed("BBC News", "feeds.bbci.co.uk", "/news/world/rss.xml", ("www.bbc.co.uk", "www.bbc.com")),
    Feed("The Guardian", "www.theguardian.com", "/world/rss", ("www.theguardian.com",)),
    Feed("NPR", "feeds.npr.org", "/1001/rss.xml", ("www.npr.org",)),
)
LIMIT = 768 * 1024


def _read(connection, path, source):
    try:
        connection.request("GET", path, headers={
            "User-Agent": "EmberDen/1.0 (https://github.com/marticampgin/ember_den)",
            "Accept": "application/json, application/rss+xml, application/xml",
        })
        response = connection.getresponse()
        if response.status != 200:
            help_text = {("SearXNG", 403): " Enable the JSON output format on the local server.",
                         ("Mwmbl", 429): " The anonymous search rate or quota was reached."}
            raise ValueError(f"{source} returned HTTP {response.status}." + help_text.get((source, response.status), ""))
        raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError(f"{source} returned too much data.")
        return raw
    except (http.client.HTTPException, OSError) as exc:
        raise ValueError(f"Could not reach {source}.") from exc
    finally:
        connection.close()


def _plain(value, limit):
    if not isinstance(value, str):
        return ""
    value = " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())
    if len(value) <= limit:
        return value
    fragment = value[:limit-1]
    return (fragment.rpartition(" ")[0] or fragment).rstrip(",;:") + "…"


def public_host(value):
    """Only return ordinary public HTTPS hostnames suitable for linking."""
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if parts.scheme != "https" or parts.username is not None or parts.password is not None or parts.port not in (None,443):
            return ""
    except ValueError:
        return ""
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host):
        return ""
    return "" if host.endswith((".local", ".localhost", ".internal", ".test", ".invalid", ".onion", ".home", ".lan")) else host


def _reference(provider, publisher, title, url, snippet, published=""):
    return {"kind": "web", "id": sha256(url.encode()).hexdigest()[:24], "name": title,
            "text": snippet, "url": url, "provider": provider, "publisher": publisher,
            "published_at": published}


def _result(hits, notice, stage):
    entries = []
    for hit in hits:
        attribution = " · ".join(filter(None, (hit["publisher"], hit["published_at"][:10])))
        entries.append("\n".join(filter(None, (hit["name"], attribution, hit["text"], hit["url"]))))
    return {"context": hits, "notice": notice, "response": "\n\n".join([notice, *entries]),
            "events": [{"stage": stage, "detail": notice}]}


def parse_feed(feed, raw):
    # These fixed endpoints serve UTF-8 RSS. Reject DTDs and alternate encodings.
    if b"\0" in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Unsupported feed document.")
    try:
        channel = ET.fromstring(raw).find("channel")
    except ET.ParseError as exc:
        raise ValueError("Unreadable RSS feed.") from exc
    if channel is None:
        raise ValueError("Missing RSS channel.")
    hits = []
    for item in channel.findall("item")[:100]:
        url = (item.findtext("link") or "").strip()
        title = _plain(item.findtext("title"), 220)
        if not title or public_host(url) not in feed.article_hosts:
            continue
        try:
            date = parsedate_to_datetime(item.findtext("pubDate") or "")
            if date.tzinfo is None:
                continue
            published = date.astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError, OverflowError):
            continue
        hits.append(_reference("Publisher RSS", feed.name, title, url, _plain(item.findtext("description"), 180), published))
    return hits


class PublisherNews:
    def __init__(self):
        self.cached = {}
        self.lock = threading.Lock()

    def _feed(self, feed):
        with self.lock:
            cached = self.cached.get(feed.name)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        raw = _read(http.client.HTTPSConnection(feed.host, timeout=7), feed.path, feed.name)
        hits = parse_feed(feed, raw)
        with self.lock:
            self.cached[feed.name] = time.monotonic(), hits
        return hits

    def search(self, query):
        hits, sources, unavailable = [], [], []
        with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
            jobs = [(feed, pool.submit(self._feed, feed)) for feed in FEEDS]
            for feed, job in jobs:
                try:
                    hits.extend(job.result())
                    sources.append(feed.name)
                except ValueError:
                    unavailable.append(feed.name)
        if not sources:
            raise ValueError("None of the BBC News, The Guardian or NPR feeds could be read.")
        now = datetime.now(timezone.utc)
        latest = query.strip().casefold() in ("headlines", "latest", "latest headlines", "news", "top stories")
        terms = set() if latest else set(re.findall(r"[^\W_]+", query.casefold()))
        if not latest and not terms:
            raise ValueError("Enter a news topic or use ‘headlines’.")
        candidates = []
        for hit in hits:
            date = datetime.fromisoformat(hit["published_at"])
            words = set(re.findall(r"[^\W_]+", (hit["name"] + " " + hit["text"]).casefold()))
            if now - timedelta(days=7) <= date <= now + timedelta(minutes=10) and terms <= words:
                candidates.append(hit)
        candidates.sort(key=lambda hit: hit["published_at"], reverse=True)
        # Include a leading story from each matching publisher, then fill in date order.
        selected, publishers = {}, set()
        for hit in candidates:
            if hit["publisher"] not in publishers:
                selected[hit["url"]] = hit
                publishers.add(hit["publisher"])
        for hit in candidates:
            if len(selected) >= 5:
                break
            selected.setdefault(hit["url"], hit)
        hits = sorted(selected.values(), key=lambda hit: hit["published_at"], reverse=True)
        notice = f"{len(hits)} matching headlines from the past seven days. Feeds checked: {', '.join(sources)}."
        if unavailable:
            notice += " Unavailable: " + ", ".join(unavailable) + "."
        return _result(hits, notice, "Publisher feed lookup")


class WebSearch:
    def __init__(self, port=None):
        self.port = os.environ.get("EMBER_SEARXNG_PORT", "") if port is None else str(port)
        self.name = "SearXNG" if self.port else "Mwmbl"

    def search(self, query):
        if self.port:
            if not re.fullmatch(r"[0-9]{1,5}", self.port) or not 1 <= int(self.port) <= 65535:
                raise ValueError("EMBER_SEARXNG_PORT must be a local TCP port from 1 to 65535.")
            connection = http.client.HTTPConnection("127.0.0.1", int(self.port), timeout=8)
            path = "/search?" + urlencode({"q": query, "format": "json", "categories": "general", "language": "en", "pageno": 1})
        else:
            connection = http.client.HTTPSConnection("api.mwmbl.org", timeout=8)
            path = "/api/v2/search/?" + urlencode({"q": query})
        raw = _read(connection, path, self.name)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"{self.name} returned unreadable search results.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError(f"{self.name} returned an unexpected search response.")
        hits, seen = [], set()
        for item in payload["results"][:100]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            host = public_host(url)
            title = _plain(item.get("title"), 220)
            if not host or not title or url in seen:
                continue
            seen.add(url)
            hits.append(_reference(self.name, host, title, url, _plain(item.get("content"), 210)))
            if len(hits) >= 5:
                break
        notice = f"{self.name} returned {len(hits)} public HTTPS search results."
        if self.name == "SearXNG" and payload.get("unresponsive_engines"):
            notice += " Some upstream engines did not respond."
        return _result(hits, notice, "Web search")
