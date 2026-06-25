"""
One-time helper to obtain a Strava REFRESH TOKEN for the server.

Run locally:  python get_strava_token.py
You'll need your Strava app's Client ID and Client Secret
(from https://www.strava.com/settings/api).
"""
import sys
import webbrowser
import urllib.parse
import requests

cid = input("Strava Client ID: ").strip()
csecret = input("Strava Client Secret: ").strip()

redirect = "http://localhost/exchange_token"
auth = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
    "client_id": cid,
    "redirect_uri": redirect,
    "response_type": "code",
    "approval_prompt": "auto",
    "scope": "read,activity:read_all,profile:read_all",
})
print("\n1) Opening this URL — click Authorize:\n", auth, "\n")
try:
    webbrowser.open(auth)
except Exception:
    pass
print("2) Your browser will redirect to a 'localhost' page that fails to load.")
print("   Copy the value of `code=` from that URL's address bar and paste it here.\n")
code = input("Paste the code: ").strip()

r = requests.post("https://www.strava.com/oauth/token", data={
    "client_id": cid,
    "client_secret": csecret,
    "code": code,
    "grant_type": "authorization_code",
}, timeout=30)
r.raise_for_status()
tok = r.json()
print("\n=== Add these to your server environment ===")
print("STRAVA_CLIENT_ID =", cid)
print("STRAVA_CLIENT_SECRET =", csecret)
print("STRAVA_REFRESH_TOKEN =", tok["refresh_token"])
