"""Локальный веб-интерфейс: http://127.0.0.1:<port>/"""
import json

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, geo, rules, settings
from .paths import WEB
from .runner import manager
from .scrapers import SCRAPERS

app = FastAPI(title="RealtyWatch")
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/listings")
def listings():
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM listings ORDER BY first_seen DESC")]
    hist = {}
    for r in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
        hist.setdefault((r["source"], r["source_id"]), []).append([r["ts"], r["price"]])
    con.close()
    for r in rows:
        r["flags"] = json.loads(r["flags"]) if r["flags"] else {}
        r["raw"] = json.loads(r["raw"]) if r["raw"] else {}
        r["history"] = hist.get((r["source"], r["source_id"]), [])
    return rows


@app.get("/api/settings")
def get_settings():
    return settings.load()


@app.post("/api/settings")
def set_settings(cfg: dict = Body(...)):
    cur = settings.load()
    for k in ("filters", "zone", "sites", "browser"):
        if k in cfg:
            cur[k] = cfg[k]
    settings.save(cur)
    return cur


@app.post("/api/reclassify")
def reclassify():
    """Пересчитать зону и отсев для всей базы по текущим настройкам (без обращения к сайтам)."""
    cfg = settings.load()
    con = db.connect()
    n = 0
    for r in con.execute("SELECT * FROM listings").fetchall():
        item = dict(r)
        item["flags"] = {}
        item["zone"] = geo.classify(item, cfg["zone"])
        reasons = rules.evaluate(item, cfg["filters"])
        con.execute("UPDATE listings SET zone=?, excluded=?, flags=? WHERE source=? AND source_id=?",
                    (item["zone"], "; ".join(reasons) or None, json.dumps(item["flags"], ensure_ascii=False),
                     item["source"], item["source_id"]))
        n += 1
    con.commit()
    con.close()
    return {"updated": n}


@app.post("/api/run")
def run(body: dict = Body(...)):
    sources = body.get("sources") or list(SCRAPERS)
    return {"started": manager.start(sources)}


@app.post("/api/stop")
def stop(body: dict = Body(default={})):
    manager.stop(body.get("sources"))
    return {"ok": True}


@app.get("/api/status")
def status(after: int = 0):
    return manager.snapshot(after)


@app.get("/api/runs")
def runs():
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 50")]
    con.close()
    return rows
