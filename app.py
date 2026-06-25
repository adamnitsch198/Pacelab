"""
Pacelab live server.

Serves the dashboard at  /         and the live data at  /api/data .
A background scheduler refreshes the data from Strava + Garmin every hour.
The last successful payload is cached in memory and on disk, so a transient
Garmin/Strava failure never takes the dashboard down.
"""
import os
import json
import logging
import threading
import datetime as dt

from fastapi import FastAPI, Response, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler

from fetchers import build_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pacelab")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.environ.get("CACHE_FILE", "/tmp/pacelab-data.json")
REFRESH_MINUTES = int(os.environ.get("REFRESH_MINUTES", "60"))

app = FastAPI(title="Pacelab")
_lock = threading.Lock()
_payload = None


def _load_cache():
    global _payload
    try:
        with open(CACHE_FILE) as f:
            _payload = json.load(f)
            log.info("Loaded cached payload from %s", CACHE_FILE)
    except Exception:
        _payload = None


def refresh():
    """Pull fresh data and update the in-memory + on-disk cache."""
    global _payload
    log.info("Refreshing data from Strava + Garmin…")
    try:
        data = build_payload()
        with _lock:
            _payload = data
            try:
                with open(CACHE_FILE, "w") as f:
                    json.dump(data, f)
            except Exception as e:
                log.warning("Could not write cache: %s", e)
        log.info("Refresh complete: %d activities, garmin=%s",
                 len(data.get("activities", [])), bool(data.get("garmin")))
    except Exception as e:
        log.error("Refresh FAILED (keeping previous data): %s", e)


@app.on_event("startup")
def _startup():
    _load_cache()
    # First refresh in a background thread so the server can accept requests
    # immediately even if the initial Strava/Garmin pull is slow.
    threading.Thread(target=refresh, daemon=True).start()
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(refresh, "interval", minutes=REFRESH_MINUTES, id="refresh",
                  next_run_time=dt.datetime.now() + dt.timedelta(minutes=REFRESH_MINUTES))
    sched.start()
    log.info("Scheduler started: refreshing every %d min", REFRESH_MINUTES)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/api/data")
def api_data():
    with _lock:
        if _payload is None:
            raise HTTPException(status_code=503, detail="Data not ready yet — first fetch in progress.")
        return JSONResponse(_payload, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz():
    with _lock:
        ready = _payload is not None
    return {"ok": True, "data_ready": ready, "updated": (_payload or {}).get("updated")}


@app.post("/api/refresh")
def manual_refresh(x_refresh_token: str = Header(default="")):
    """Optional manual trigger. Protected by REFRESH_SECRET if it is set."""
    secret = os.environ.get("REFRESH_SECRET")
    if secret and x_refresh_token != secret:
        raise HTTPException(status_code=401, detail="bad token")
    threading.Thread(target=refresh, daemon=True).start()
    return {"triggered": True}
