# Pacelab — live, hourly-updating training dashboard

A small web service that re-pulls your **Strava** activities and **Garmin**
recovery data every hour and serves your Pacelab dashboard, live from anywhere.

```
  Strava API ─┐
              ├─►  fetchers.py ──►  /api/data (JSON, cached)  ──►  dashboard (static/index.html)
  Garmin ─────┘        ▲
                       └── refreshed every 60 min by an in-process scheduler
```

The dashboard fetches `/api/data` on load and reloads itself hourly; the server
refreshes the underlying data on its own hourly schedule. The last good payload
is cached in memory and on disk, so a transient Strava/Garmin hiccup never blanks
the page.

---

## What you need (one-time)

### 1. Strava API access
1. Go to <https://www.strava.com/settings/api> and create an app (any name; set
   "Authorization Callback Domain" to `localhost`). Note the **Client ID** and
   **Client Secret**.
2. On your computer, with Python installed:
   ```bash
   pip install requests
   python get_strava_token.py
   ```
   Follow the prompts (authorize in the browser, paste the `code` from the
   redirected URL). It prints your **STRAVA_REFRESH_TOKEN**.

### 2. Garmin tokens (no password on the server)
On the **Mac where the Garmin MCP already works**:
```bash
python pack_garmin_tokens.py
```
Copy the long printed value — that's your **GARMIN_TOKENSTORE_B64**. It bundles
your existing OAuth tokens (which last ~a year and refresh themselves), so the
server never needs your Garmin password or an MFA code.

> If you'd rather not use tokens, you can set `GARMIN_EMAIL` / `GARMIN_PASSWORD`
> instead — but that path fails if your account uses MFA.

---

## Deploy (Render — recommended)

1. Put this folder in a **private GitHub repo** (secrets go in env vars, never in
   the repo).
2. At <https://render.com> → **New → Web Service** → connect the repo.
3. Render auto-detects the `Dockerfile`. Choose an **instance type that stays
   awake** (see note below).
4. Under **Environment**, add:
   | Key | Value |
   |-----|-------|
   | `STRAVA_CLIENT_ID` | from step 1 |
   | `STRAVA_CLIENT_SECRET` | from step 1 |
   | `STRAVA_REFRESH_TOKEN` | from step 1 |
   | `GARMIN_TOKENSTORE_B64` | from step 2 |
   | `ATHLETE_NAME` | `Adam` |
   | `REFRESH_MINUTES` | `60` |
5. **Create Web Service.** First boot fetches your data (a few seconds); the page
   shows a brief "first fetch in progress" message until it's ready, then your
   live dashboard appears at the Render URL.

**Railway** and **Fly.io** work the same way (Docker + the same env vars) and are
good alternatives.

### ⚠️ Keep-awake note
Free tiers (incl. Render Free) **sleep after inactivity**, which pauses the hourly
scheduler. For genuine hourly updates, either use a small paid always-on instance
(~$5–7/mo on Render/Railway/Fly) or keep a free instance awake with an uptime
pinger (e.g. UptimeRobot) hitting `/healthz` every few minutes.

---

## Run locally (to test before deploying)
```bash
pip install -r requirements.txt
cp .env.example .env          # fill in your values
export $(grep -v '^#' .env | xargs)   # load them (mac/linux)
uvicorn app:app --reload --port 8000
# open http://localhost:8000
```

## Endpoints
- `GET /` — the dashboard
- `GET /api/data` — current JSON payload
- `GET /healthz` — `{ok, data_ready, updated}` (use for uptime pings)
- `POST /api/refresh` — force a refresh now (send header `X-Refresh-Token` if you
  set `REFRESH_SECRET`)

## Notes & limitations
- **Run pace zones** come from `config/zones.json` (Strava's public API doesn't
  expose them). Edit that file if your zones change; HR zones auto-update from
  Strava when available.
- **Aerobic decoupling** is computed from your most recent long run (≥ ~12.5 mi).
- **Garmin is unofficial.** If a future Garmin change breaks the library, the
  dashboard keeps serving the last good data and logs the error; updating
  `garminconnect` (`pip install -U garminconnect`) usually fixes it.
- Keep the repo **private** and your env vars secret. Anyone with the deployed URL
  can see your dashboard — add auth at the host level if you want it locked down.
