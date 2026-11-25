"""Explicitly invoked public reads and user-approved exports for the local companion."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.parse import urlencode
import uuid

from .engine import protected


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{label} must contain 1 to {limit:,} characters.")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        raise ValueError(f"{label} contains a control character.")
    return value.strip()


class PublicSourceError(RuntimeError):
    pass


def public_json(host, path, parameters, source):
    allowed = {("en.wikipedia.org","/w/api.php"), ("geocoding-api.open-meteo.com","/v1/search"),
               ("api.open-meteo.com","/v1/forecast")}
    if (host,path) not in allowed:
        raise ValueError("Unknown public source.")
    conn = http.client.HTTPSConnection(host, timeout=8)
    try:
        conn.request("GET", path + "?" + urlencode(parameters), headers={
            "User-Agent":"EmberDen/1.0 (https://github.com/marticampgin/ember_den)", "Accept":"application/json",
        })
        response = conn.getresponse()
        if response.status != 200:
            raise PublicSourceError(f"{source} returned HTTP {response.status}.")
        data = response.read(262145)
        if len(data) > 262144:
            raise PublicSourceError(f"{source} returned too much data.")
        result = json.loads(data)
        if not isinstance(result,dict):
            raise PublicSourceError(f"{source} returned an unexpected response.")
        return result
    except (OSError,http.client.HTTPException,ValueError) as exc:
        raise PublicSourceError(f"Could not read the public {source} API.") from exc
    finally:
        conn.close()


class Wikipedia:
    def request(self, **parameters):
        payload = public_json("en.wikipedia.org","/w/api.php",
                              {"action":"query","format":"json","formatversion":2,**parameters}, "Wikipedia")
        if not isinstance(payload.get("query"),dict):
            raise PublicSourceError("Wikipedia returned no query data.")
        return payload["query"]

    @staticmethod
    def reference(page, content):
        page_id = page.get("pageid")
        if type(page_id) is not int or page_id <= 0:
            raise PublicSourceError("Wikipedia returned an invalid page identifier.")
        title = text(page.get("title"), "Article title", 512)
        return {"kind":"web", "id":str(page_id), "name":title,
                "url":f"https://en.wikipedia.org/?curid={page_id}", "text":content[:1400]}

    def search(self, query):
        data = self.request(list="search", srsearch=query, srlimit=3, srnamespace=0, srprop="snippet")
        hits = []
        for page in data.get("search", [])[:3]:
            snippet = unescape(re.sub(r"<[^>]*>", "", str(page.get("snippet", ""))))
            hits.append(self.reference(page, snippet))
        return hits

    def read(self, page_id):
        data = self.request(pageids=int(page_id), prop="extracts", exintro=1, explaintext=1,
                            exchars=850, exlimit=1)
        pages = data.get("pages", [])
        if not pages or not pages[0].get("extract"):
            raise PublicSourceError("This Wikipedia article has no readable introduction.")
        return self.reference(pages[0], pages[0]["extract"])


class OpenMeteo:
    """Two public reads resolve a place name and return its dated temperature outlook."""
    def forecast(self, place):
        name, separator, country = place.rpartition(",")
        lookup = {"name":place, "count":1, "language":"en", "format":"json"}
        if separator and re.fullmatch("[A-Za-z]{2}", country.strip()):
            lookup.update(name=name.strip(), countryCode=country.strip().upper())
        matches = public_json("geocoding-api.open-meteo.com","/v1/search",lookup,"Open-Meteo").get("results",[])
        if not matches:
            raise PublicSourceError("No place found. Try a city name with an optional country code, such as Berlin, DE.")
        try:
            location = matches[0]
            lat, lon = float(location["latitude"]), float(location["longitude"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
            label = ", ".join(dict.fromkeys(str(location[key]) for key in ("name","admin1","country_code") if location.get(key)))
            parameters = {"latitude":round(lat,5), "longitude":round(lon,5), "current":"temperature_2m",
                          "daily":"temperature_2m_max,temperature_2m_min", "forecast_days":3, "timezone":"auto"}
            result = public_json("api.open-meteo.com","/v1/forecast",parameters,"Open-Meteo")
            current, daily = result["current"], result["daily"]
            today = datetime.fromisoformat(current["time"]).strftime("%Y-%m-%d %H:%M")
            lines = [f"{today} ({result['timezone']}) · Current temperature {float(current['temperature_2m']):g} °C"]
            for day, low, high in zip(daily["time"][:3],daily["temperature_2m_min"][:3],daily["temperature_2m_max"][:3],strict=True):
                lines.append(f"{date.fromisoformat(day)} · Low {float(low):g} °C / high {float(high):g} °C")
            if len(lines)!=4:
                raise ValueError
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            raise PublicSourceError("Open-Meteo did not return a complete three-day outlook.") from exc
        source = "https://api.open-meteo.com/v1/forecast?" + urlencode(parameters)
        ref = {"kind":"web", "id":f"weather:{lat}:{lon}:{current['time']}", "name":"Open-Meteo · "+label,
               "text":"\n".join(lines), "url":source}
        return {"response":ref["name"]+"\n"+ref["text"]+"\n"+source, "context":[ref], "events":[
            {"stage":"Geocoding", "detail":f"Open-Meteo resolved {place!r} to {label} ({lat:g}, {lon:g})."},
            {"stage":"Weather lookup", "detail":f"Retrieved {today} local conditions and three dated daily forecasts."},
        ]}


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    command: str
    usage: str
    permission: str
    fields: tuple
    handler: object

    def describe(self):
        schema = {"type":"object", "additionalProperties":False,
                  "properties":{key:{"type":"string", "maxLength":limit} for key, limit in self.fields},
                  "required":[key for key,_ in self.fields]}
        return {"name":self.name, "label":self.label, "command":self.command,
                "usage":self.usage, "permission":self.permission, "schema":schema}

    def validate(self, raw):
        if not isinstance(raw, dict) or set(raw) != {key for key,_ in self.fields}:
            raise ValueError(f"Expected arguments: {', '.join(key for key,_ in self.fields)}.")
        return {key:text(raw[key],key,limit) for key,limit in self.fields}


def ical_text(value):
    return value.replace("\\","\\\\").replace("\r\n","\n").replace("\r","\n").replace("\n","\\n").replace(",","\\,").replace(";","\\;")


def fold_line(value):
    # RFC 5545 counts UTF-8 octets including continuation whitespace.
    lines, line = [], ""
    for char in value:
        if len((line + char).encode("utf-8")) > 75:
            lines.append(line)
            line = " "
        line += char
    return "\r\n".join([*lines, line])


class Tools:
    def __init__(self, data_dir, public_source=None, weather_source=None):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "ember.sqlite3"
        self.exports = self.root / "exports"
        self.public = public_source or Wikipedia()
        self.weather = weather_source or OpenMeteo()
        self.registry = {}
        for tool in (
            Tool("wiki_search","Search Wikipedia","/web","/web red fox","public read",(("query",240),),self._search),
            Tool("wiki_research","Read the first article","/research","/research red fox","public read",(("query",240),),self._research),
            Tool("weather","Check local weather","/weather","/weather Berlin, DE","public read",(("place",120),),self._weather),
            Tool("export_note","Export a text note","/note","/note Title | Note text","approval required",(("title",120),("body",8000)),self._note),
            Tool("export_event","Export a calendar event","/event","/event YYYY-MM-DD | Event title","approval required",(("date",10),("title",120)),self._event),
        ):
            self.register(tool)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tool_proposals (
                    id TEXT PRIMARY KEY, tool TEXT NOT NULL, title TEXT NOT NULL, filename TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_activity (
                    id TEXT PRIMARY KEY, at TEXT NOT NULL, tool TEXT NOT NULL, permission TEXT NOT NULL,
                    arguments TEXT NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL, elapsed_ms INTEGER NOT NULL
                );
            """)

    def register(self, tool):
        if tool.name in self.registry or tool.permission not in {"public read", "approval required"}:
            raise ValueError("Duplicate tool or unsupported permission.")
        self.registry[tool.name] = tool

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _log(db, tool, permission, args, summary, status, since):
        db.execute("INSERT INTO tool_activity VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (uuid.uuid4().hex, now(), tool, permission, json.dumps(protected(args)),
                    protected(summary), status, round((time.monotonic()-since)*1000)))

    def _lookup(self, args, read):
        query = args["query"]
        if query != protected(query):
            raise ValueError("Remove credentials from the public search query.")
        hits = self.public.search(query)
        events = [{"stage":"Public search", "detail":f"Wikipedia search for {query!r} returned {len(hits)} articles."}]
        prefix = ""
        if read and hits:
            try:
                article = self.public.read(hits[0]["id"])
                hits = [article]
                events.append({"stage":"Read source", "detail":f"Retrieved {article['name']} from {article['url']}."})
            except PublicSourceError as exc:
                prefix = f"The article introduction was unavailable. Showing search results. {exc}\n\n"
                events.append({"stage":"Read source", "detail":prefix.strip()})
        response = "\n\n".join(f"{hit['name']}\n{hit['text']}\n{hit['url']}" for hit in hits)
        return {"response":prefix + (response or "Wikipedia returned no matching articles."), "context":hits, "events":events}

    def _search(self, args):
        return self._lookup(args,False)

    def _research(self, args):
        return self._lookup(args,True)

    def _weather(self, args):
        if args["place"] != protected(args["place"]):
            raise ValueError("Remove credentials from the public place query.")
        return self.weather.forecast(args["place"])

    def _note(self, args):
        return args["title"], ".txt", args["title"] + "\n\n" + args["body"] + "\n"

    def _event(self, args):
        try:
            start = date.fromisoformat(args["date"])
            if start.isoformat() != args["date"]:
                raise ValueError
            end = start + timedelta(days=1)
        except (ValueError, OverflowError) as exc:
            raise ValueError("Use a valid all-day event date in YYYY-MM-DD format.") from exc
        lines = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Ember Den//Local calendar//EN",
                 "CALSCALE:GREGORIAN","BEGIN:VEVENT",f"UID:{uuid.uuid4().hex}@ember.local",
                 "DTSTAMP:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                 "DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"), "DTEND;VALUE=DATE:" + end.strftime("%Y%m%d"),
                 "SUMMARY:" + ical_text(args["title"]),"END:VEVENT","END:VCALENDAR"]
        return args["title"], ".ics", "\r\n".join(map(fold_line,lines)) + "\r\n"

    def _propose(self, db, tool, specification):
        title, extension, payload = specification
        if extension not in {".txt",".ics"}:
            raise ValueError("Unsupported export file type.")
        identifier = uuid.uuid4().hex
        slug = re.sub(r"[^A-Za-z0-9_-]+","-",title).strip("-")[:48] or "export"
        filename = identifier[:12] + "-" + slug + extension
        db.execute("INSERT INTO tool_proposals VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                   (identifier,tool.name,title,filename,payload,now()))
        return {"response":f"Prepared {filename}. Review in Tools to approve or decline the export.",
                "proposal_id":identifier, "events":[{"stage":"Approval", "detail":f"Pending approval for exports/{filename}."}]}

    def run(self, name, arguments):
        if not isinstance(name,str) or name not in self.registry:
            raise ValueError("Choose a registered tool.")
        tool = self.registry[name]
        started = time.monotonic()
        args = tool.validate(arguments)
        try:
            result = tool.handler(args)
            with self.db() as db:
                if tool.permission == "approval required":
                    result = self._propose(db, tool, result)
                summary = " ".join(event["detail"] for event in result["events"])
                self._log(db,name,tool.permission,args,summary,"completed",started)
            return {**result,"tool":name,"status":"completed"}
        except (ValueError,PublicSourceError) as exc:
            message = str(exc)
            with self.db() as db:
                self._log(db,name,tool.permission,args,message,"error",started)
            return {"tool":name,"status":"error","error":message,"response":message,"events":[{"stage":"Tool error","detail":message}]}

    def _export(self, row):
        if self.exports.resolve() != self.exports:
            raise ValueError("The export directory must be a local folder inside the data directory.")
        self.exports.mkdir(exist_ok=True)
        name = row["filename"]
        if not re.fullmatch(r"[a-f0-9]{12}-[A-Za-z0-9_-]{1,48}\.(txt|ics)", name):
            raise ValueError("Invalid export filename.")
        target = self.exports / name
        if target.is_symlink() or target.resolve() != target:
            raise ValueError("The export destination is occupied.")
        payload = row["payload"].encode("utf-8")
        def already_written():
            return target.is_file() and target.stat().st_size == len(payload) and target.read_bytes() == payload
        if target.exists():
            if already_written():
                return
            raise ValueError("A different file already occupies the export destination.")
        with tempfile.NamedTemporaryFile(dir=self.exports, prefix=".ember-", delete=False) as pending:
            tmp = Path(pending.name)
            try:
                pending.write(payload)
                pending.flush()
                os.fsync(pending.fileno())
            except BaseException:
                pending.close()
                tmp.unlink(missing_ok=True)
                raise
        try:
            if os.name == "nt":
                os.rename(tmp,target)  # Windows rename is atomic and refuses an existing target.
            else:
                os.link(tmp,target)
        finally:
            tmp.unlink(missing_ok=True)

    def decide(self, identifier, decision):
        if decision not in {"approve", "decline"}:
            raise ValueError("Choose approve or decline.")
        start = time.monotonic()
        status = "approved" if decision=="approve" else "declined"
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tool_proposals WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("Proposal does not exist.")
            if row["status"] not in {"pending",status}:
                return 409, {"error":f"This proposal was already {row['status']}."}
            if row["status"] == "pending":
                if status=="approved":
                    try:
                        self._export(row)
                    except (ValueError,OSError) as exc:
                        message = str(exc) if isinstance(exc,ValueError) else "Could not create the export file."
                        self._log(db,row["tool"],"explicit approval",{"proposal":identifier},message,"error",start)
                        return 409, {"error":message}
                db.execute("UPDATE tool_proposals SET status=? WHERE id=?", (status,identifier))
                self._log(db,row["tool"],"explicit "+decision,{"proposal":identifier,"filename":row["filename"]},
                          "File exported." if decision=="approve" else "Proposal declined.","completed",start)
        return 200, {"id":identifier,"status":status,"filename":row["filename"],"directory":str(self.exports)}

    def state(self):
        with self.db() as db:
            pending = [dict(row) for row in db.execute("SELECT * FROM tool_proposals WHERE status='pending' ORDER BY rowid DESC")]
            resolved = [dict(row) for row in db.execute("SELECT id,tool,title,filename,created_at,status FROM tool_proposals WHERE status!='pending' ORDER BY rowid DESC LIMIT 15")]
            recent = [{**dict(row),"arguments":json.loads(row["arguments"])} for row in db.execute("SELECT * FROM tool_activity ORDER BY rowid DESC LIMIT 35")]
        return {"tools":[item.describe() for item in self.registry.values()], "directory":str(self.exports),
                "pending":pending,"resolved":resolved,"activity":recent}

    def route(self,method,path,body):
        if path=="/api/tools" and method=="GET":
            return 200,self.state()
        if path=="/api/tools/run" and method=="POST":
            result = self.run(body.get("tool"),body.get("arguments"))
            return (200 if result["status"]=="completed" else 422),result
        match = re.fullmatch(r"/api/tools/proposals/([a-f0-9]{32})/(approve|decline)",path)
        if match and method=="POST":
            return self.decide(match[1],match[2])
        return None

    def _parse(self, prompt):
        raw = prompt.strip()
        command, _, value = raw.partition(" ")
        tool = next((item for item in self.registry.values() if item.command == command.lower()), None)
        if tool:
            values = [value] if len(tool.fields)==1 else value.split("|",maxsplit=len(tool.fields)-1)
            return tool, {key:v for (key,_),v in zip(tool.fields,values)}
        phrases = (
            (r"search wikipedia for\s+(.+)", "wiki_search"),
            (r"research wikipedia for\s+(.+)", "wiki_research"),
            (r"(?:what(?: is|['’]s) the )?weather(?: like)? in\s+(.+?)\??", "weather"),
            (r"draft a note titled\s+(.+?)\s+(?:with (?:the )?(?:text|body)|saying)\s+(.+)", "export_note"),
        )
        for pattern, name in phrases:
            match = re.fullmatch(pattern,raw,flags=re.IGNORECASE|re.DOTALL)
            if match:
                tool = self.registry[name]
                values = [value.strip().strip('“”"') for value in match.groups()]
                return tool,{key:v for (key,_),v in zip(tool.fields,values)}
        return None

    def accepts(self, prompt):
        return self._parse(prompt) is not None

    def chat(self, prompt):
        invocation = self._parse(prompt)
        if invocation is None:
            return None
        tool, args = invocation
        try:
            return self.run(tool.name,args)
        except ValueError as exc:
            message = f"{exc} Try {tool.usage}."
            return {"tool":tool.name,"status":"error","response":message,"error":message,
                    "events":[{"stage":"Validate arguments","detail":message}]}

