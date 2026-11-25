"""Read-only aggregate measurements from stored execution records."""
import ctypes
import json
import os
import statistics


def available_ram():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load",ctypes.c_uint32)] + [
                (name,ctypes.c_uint64) for name in ("total","available","page","page_available","virtual",
                                                    "virtual_available","reserved")]
        value=MemoryStatus()
        value.length=ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return {"available":value.available,"total":value.total}
    return None


def overview(store):
    groups={}
    with store.connect() as db:
        rows=db.execute("SELECT model,status,metrics FROM runs ORDER BY rowid DESC LIMIT 200").fetchall()
        tools=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_activity'").fetchone()
        activity=[] if not tools else [dict(r) for r in db.execute(
            "SELECT tool,status,COUNT(*) AS count FROM tool_activity GROUP BY tool,status ORDER BY tool,status")]
    for row in rows:
        key=row["model"] or "Unselected"
        group=groups.setdefault(key,{"count":0,"completed":0,"errors":0,"times":[],"first":[]})
        group["count"]+=1
        if row["status"]=="completed":
            group["completed"]+=1
            measurement=json.loads(row["metrics"])
            for src,dst in (("total_ms","times"),("ttft_ms","first")):
                if isinstance(measurement.get(src),(int,float)):
                    group[dst].append(measurement[src])
        elif row["status"]=="error":
            group["errors"]+=1
    for group in groups.values():
        for src,dst in (("times","median_ms"),("first","median_first_ms")):
            values=group.pop(src)
            group[dst]=statistics.median(values) if values else None
    return {"runs":len(rows),"models":groups,"tools":activity,"system_memory":available_ram()}
