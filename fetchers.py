"""
Data fetchers for Pacelab.

Pulls activities + zones from the Strava API and recovery/health metrics from
Garmin Connect (via the unofficial `garminconnect` library), then shapes them
into exactly the JSON the dashboard front-end expects:

    { "today", "updated", "activities", "zones", "garmin", "analytics" }

Every Garmin sub-call is wrapped defensively: if one metric fails, that field is
set to null rather than crashing the whole refresh.
"""
import os
import json
import base64
import io
import tarfile
import datetime as dt
import logging

import requests

from compute import decoupling_from_streams

log = logging.getLogger("pacelab.fetch")

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API = "https://www.strava.com/api/v3"

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
#  Strava
# --------------------------------------------------------------------------- #
def _strava_access_token() -> str:
    r = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "grant_type": "refresh_token",
        "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _strava_get(path: str, token: str, **params):
    r = requests.get(f"{STRAVA_API}{path}", headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_strava(token: str, n_activities: int = 80) -> dict:
    """Return (activities list, zones dict, latest-long-run id)."""
    raw = _strava_get("/athlete/activities", token, per_page=min(n_activities, 100), page=1)
    acts = []
    for a in raw:
        acts.append({
            "id": str(a["id"]),
            "name": a.get("name", "Activity"),
            "sport_type": a.get("sport_type") or a.get("type") or "Run",
            "start_local": a.get("start_date_local"),
            "distance": a.get("distance", 0.0),
            "moving_time": a.get("moving_time", 0),
            "elapsed_time": a.get("elapsed_time", 0),
            "elevation_gain": a.get("total_elevation_gain", 0.0),
            "avg_speed": a.get("average_speed", 0.0),
            "relative_effort": a.get("suffer_score"),       # Strava "Relative Effort"
            "total_calories": None,                          # not in summary endpoint
            "avg_cadence": a.get("average_cadence"),
            "achievement_count": a.get("achievement_count", 0),
            "pr_count": a.get("pr_count", 0),
        })

    # Heart-rate zones (Strava only exposes HR/power zones, not run pace zones)
    hr_zones = None
    try:
        z = _strava_get("/athlete/zones", token)
        zhr = (z.get("heart_rate") or {}).get("zones")
        if zhr:
            hr_zones = [{"min": x.get("min", 0), "max": (x.get("max") if x.get("max", -1) != -1 else 205)}
                        for x in zhr]
    except Exception as e:
        log.warning("Strava zones failed: %s", e)

    return {"activities": acts, "hr_zones": hr_zones}


def fetch_decoupling(token: str, activities: list) -> dict | None:
    """Compute aerobic decoupling from the most recent long run (>= ~13 mi)."""
    runs = [a for a in activities if a["sport_type"] == "Run" and a["distance"] >= 20000]
    if not runs:
        return None
    runs.sort(key=lambda a: a["start_local"], reverse=True)
    target = runs[0]
    try:
        s = _strava_get(f"/activities/{target['id']}/streams", token,
                        keys="heartrate,velocity_smooth,distance,time",
                        key_by_type="true", resolution="medium")
        hr = s.get("heartrate", {}).get("data", [])
        vel = s.get("velocity_smooth", {}).get("data", [])
        dist = s.get("distance", {}).get("data", [])
        if not (hr and vel and dist):
            return None
        miles = target["distance"] / 1609.34
        return decoupling_from_streams(hr, vel, dist, label=f"long run · {miles:.1f} mi")
    except Exception as e:
        log.warning("decoupling failed: %s", e)
        return None


# --------------------------------------------------------------------------- #
#  Garmin
# --------------------------------------------------------------------------- #
def _garmin_client():
    """Authenticate with Garmin, preferring a pre-generated token store."""
    from garminconnect import Garmin
    tokendir = "/tmp/garmintokens"
    b64 = os.environ.get("GARMIN_TOKENSTORE_B64")
    if b64:
        os.makedirs(tokendir, exist_ok=True)
        data = base64.b64decode(b64)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(tokendir)
        g = Garmin()
        g.login(tokendir)               # uses saved OAuth tokens, no MFA prompt
        return g
    # Fallback: email/password (will fail if MFA is enabled)
    g = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    g.login()
    return g


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as e:
        log.warning("garmin call %s failed: %s", getattr(fn, "__name__", fn), e)
        return default


_PHRASE_LABELS = {
    "DETRAINING": "Detraining", "MAINTAINING": "Maintaining", "RECOVERY": "Recovery",
    "PRODUCTIVE": "Productive", "PEAKING": "Peaking", "UNPRODUCTIVE": "Unproductive",
    "OVERREACHING": "Overreaching", "STRAINED": "Strained",
}


def _phrase_to_label(phrase):
    """Map a Garmin feedback phrase like 'RECOVERY_2' to a clean label."""
    if not phrase:
        return "—"
    key = str(phrase).split("_")[0].upper()
    return _PHRASE_LABELS.get(key, key.title())


def _status_phrase(ts: dict):
    try:
        d = ts["mostRecentTrainingStatus"]["latestTrainingStatusData"]
        rec = next(iter(d.values()))
        return rec.get("trainingStatus"), rec.get("trainingStatusFeedbackPhrase")
    except Exception:
        return None, None


def fetch_garmin(athlete_name: str, days: int = 14) -> dict:
    g = _garmin_client()
    today = dt.date.today()

    daily = []
    for i in range(days):
        d = today - dt.timedelta(days=(days - 1 - i))
        ds = d.isoformat()
        stats = _safe(g.get_stats, ds, default={}) or {}
        ts = _safe(g.get_training_status, ds, default={}) or {}
        vo2 = None
        mm = _safe(g.get_max_metrics, ds, default=None)
        try:
            vo2 = mm[0]["generic"]["vo2MaxValue"] if isinstance(mm, list) else \
                  mm["generic"]["vo2MaxValue"]
        except Exception:
            pass
        tstatus, tphrase = _status_phrase(ts)
        daily.append({
            "date": ds,
            "rhr": stats.get("restingHeartRate"),
            "stress": stats.get("averageStressLevel"),
            "bbHigh": stats.get("bodyBatteryHighestValue"),
            "bbLow": stats.get("bodyBatteryLowestValue"),
            "bbWake": stats.get("bodyBatteryAtWakeTime"),
            "vo2": vo2,
            "tstatus": tstatus,
            "tphrase": tphrase,
            "sleepSec": stats.get("sleepingSeconds"),
        })

    latest = daily[-1] if daily else {}

    # HRV (today, falling back to yesterday)
    hrv_ms, hrv_status = None, None
    for off in (0, 1):
        hv = _safe(g.get_hrv_data, (today - dt.timedelta(days=off)).isoformat())
        if hv and hv.get("hrvSummary"):
            hrv_ms = hv["hrvSummary"].get("lastNightAvg")
            hrv_status = hv["hrvSummary"].get("status")
            if hrv_ms:
                break

    # Last night's sleep (yesterday's calendar date holds last night)
    sleep = _safe(g.get_sleep_data, today.isoformat(), default={}) or {}
    last_sleep = _shape_sleep(sleep)

    device = "Garmin"
    try:
        ts0 = _safe(g.get_training_status, today.isoformat(), default={}) or {}
        device = (ts0["mostRecentTrainingLoadBalance"]["recordedDevices"][0]["deviceName"])
    except Exception:
        pass

    return {
        "athlete": {"name": athlete_name, "device": device, "vo2max": latest.get("vo2")},
        "latest": {
            "date": latest.get("date"),
            "trainingStatus": _phrase_to_label(latest.get("tphrase")),
            "vo2max": latest.get("vo2"),
            "hrvStatus": hrv_status or "—",
            "hrvMs": hrv_ms,
            "restingHr": latest.get("rhr"),
            "bodyBattery": {"high": latest.get("bbHigh"), "low": latest.get("bbLow")},
            "stress": latest.get("stress"),
        },
        "lastSleep": last_sleep,
        "daily": daily,
    }


def _shape_sleep(sleep: dict) -> dict:
    dto = (sleep.get("dailySleepDTO") or {})
    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    return {
        "date": dto.get("calendarDate"),
        "sleepSec": dto.get("sleepTimeSeconds") or 0,
        "score": overall.get("value") or 0,
        "scoreQual": (overall.get("qualifierKey") or "").upper() or "—",
        "deep": dto.get("deepSleepSeconds") or 0,
        "light": dto.get("lightSleepSeconds") or 0,
        "rem": dto.get("remSleepSeconds") or 0,
        "awake": dto.get("awakeSleepSeconds") or 0,
        "avgHrv": dto.get("avgOvernightHrv"),
        "hrvStatus": dto.get("hrvStatus") or "—",
        "restingHr": dto.get("restingHeartRate"),
        "avgStress": dto.get("avgSleepStress"),
        "respiration": dto.get("averageRespirationValue"),
        "awakeCount": dto.get("awakeCount"),
        "feedback": (dto.get("sleepScoreFeedback") or "").replace("_", " ").title(),
    }


# --------------------------------------------------------------------------- #
#  Orchestration
# --------------------------------------------------------------------------- #
def build_payload() -> dict:
    athlete_name = os.environ.get("ATHLETE_NAME", "Athlete")
    zones = json.load(open(os.path.join(HERE, "config", "zones.json")))

    token = _strava_access_token()
    sv = fetch_strava(token)
    activities = sv["activities"]
    if sv.get("hr_zones"):
        zones["hr_zones"] = sv["hr_zones"]

    analytics = {"decoupling": fetch_decoupling(token, activities)}

    garmin = None
    try:
        garmin = fetch_garmin(athlete_name)
    except Exception as e:
        log.error("Garmin fetch failed entirely: %s", e)

    now = dt.datetime.now()
    return {
        "today": now.isoformat(timespec="seconds"),
        "updated": now.isoformat(timespec="seconds"),
        "activities": activities,
        "zones": zones,
        "garmin": garmin,
        "analytics": analytics,
    }
