"""
Run this ON THE MAC where the Garmin MCP already works. It bundles your saved
Garmin OAuth tokens (~/.garminconnect) into a single base64 string to paste into
the server's GARMIN_TOKENSTORE_B64 env var. No password leaves your machine; the
tokens last roughly a year and refresh themselves.
"""
import os
import io
import tarfile
import base64

tokendir = os.path.expanduser("~/.garminconnect")
if not os.path.isdir(tokendir):
    raise SystemExit(f"No token dir at {tokendir}. Run the Garmin MCP `auth` step first.")

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    for name in os.listdir(tokendir):
        tf.add(os.path.join(tokendir, name), arcname=name)
print("\nGARMIN_TOKENSTORE_B64 = (copy the whole line below)\n")
print(base64.b64encode(buf.getvalue()).decode())
