"""
Vercel serverless function — proxies to the FastAPI backend.
Alternatively, run the FastAPI backend on Railway/Render and
set BACKEND_URL env var to point here.
"""
import os, json, asyncio, uuid
from http.server import BaseHTTPRequestHandler

# If you deploy the FastAPI backend separately, set this env var
# and this function will proxy to it. Otherwise, inline the logic below.
BACKEND_URL = os.getenv("BACKEND_URL")  # e.g. https://your-backend.railway.app

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        import urllib.request
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        if BACKEND_URL:
            # Proxy to dedicated backend
            req = urllib.request.Request(
                f"{BACKEND_URL}/join",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                result = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(result)
        else:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "BACKEND_URL not set. Deploy the FastAPI backend and set this env var."
            }).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
